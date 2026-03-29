from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any


def _deepcopy_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


def _new_version_id() -> str:
    return f"kv_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class MessageVersion:
    version_id: str
    parent_version_id: str | None
    messages: list[dict[str, Any]]
    created_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class MessageVersionGraph:
    """Append-only message version graph for one run state."""

    def __init__(self, seed_messages: list[dict[str, Any]] | None = None) -> None:
        self._versions: dict[str, MessageVersion] = {}
        self._latest_version_id: str | None = None
        if seed_messages:
            self.create_version(messages=seed_messages, created_by="seed")

    @property
    def latest_version_id(self) -> str | None:
        return self._latest_version_id

    def has_version(self, version_id: str | None) -> bool:
        return bool(version_id) and version_id in self._versions

    def version_ids(self) -> list[str]:
        return list(self._versions)

    def get_version(self, version_id: str) -> MessageVersion:
        try:
            return self._versions[version_id]
        except KeyError as exc:
            raise KeyError(f"unknown message version: {version_id}") from exc

    def get_messages(self, version_id: str) -> list[dict[str, Any]]:
        return _deepcopy_messages(self.get_version(version_id).messages)

    def latest_messages(self) -> list[dict[str, Any]]:
        if self._latest_version_id is None:
            return []
        return self.get_messages(self._latest_version_id)

    def create_version(
        self,
        *,
        messages: list[dict[str, Any]],
        parent_version_id: str | None = None,
        created_by: str | None = None,
        metadata: dict[str, Any] | None = None,
        version_id: str | None = None,
    ) -> MessageVersion:
        resolved_parent = parent_version_id if parent_version_id is not None else self._latest_version_id
        if resolved_parent is not None and resolved_parent not in self._versions:
            raise KeyError(f"cannot create child version from unknown parent: {resolved_parent}")

        version = MessageVersion(
            version_id=version_id or _new_version_id(),
            parent_version_id=resolved_parent,
            messages=_deepcopy_messages(messages),
            created_by=str(created_by).strip() or None,
            metadata=copy.deepcopy(metadata) if isinstance(metadata, dict) else {},
        )
        self._versions[version.version_id] = version
        self._latest_version_id = version.version_id
        return version

    def fork_latest(
        self,
        *,
        created_by: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MessageVersion:
        return self.create_version(
            messages=self.latest_messages(),
            created_by=created_by,
            metadata=metadata,
        )

    def activate(self, version_id: str) -> MessageVersion:
        version = self.get_version(version_id)
        self._latest_version_id = version.version_id
        return version

    def lineage(self, version_id: str | None = None) -> list[str]:
        if version_id is None:
            version_id = self._latest_version_id
        if version_id is None:
            return []

        lineage: list[str] = []
        current_id: str | None = version_id
        while current_id is not None:
            version = self.get_version(current_id)
            lineage.append(version.version_id)
            current_id = version.parent_version_id
        lineage.reverse()
        return lineage
