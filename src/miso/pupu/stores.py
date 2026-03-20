from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import InstalledServer


def default_pupu_data_dir() -> Path:
    try:
        from platformdirs import user_data_dir

        return Path(user_data_dir("miso", "miso")) / "pupu"
    except Exception:
        return Path.home() / ".miso" / "pupu"


class FileCatalogCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else (default_pupu_data_dir() / "mcp_catalog_cache.json")

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, *, etag: str | None, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"etag": etag, "payload": payload}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


class FileInstalledServerStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else (default_pupu_data_dir() / "installed_mcp_servers.json")

    def list_instances(self) -> list[InstalledServer]:
        if not self.path.is_file():
            return []
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return [
            InstalledServer.from_record(item)
            for item in raw.get("instances", [])
            if isinstance(item, dict)
        ]

    def get_instance(self, instance_id: str) -> InstalledServer | None:
        for instance in self.list_instances():
            if instance.instance_id == instance_id:
                return instance
        return None

    def save_instance(self, instance: InstalledServer) -> InstalledServer:
        instances = self.list_instances()
        replaced = False
        next_instances: list[InstalledServer] = []
        for current in instances:
            if current.instance_id == instance.instance_id:
                next_instances.append(instance)
                replaced = True
            else:
                next_instances.append(current)
        if not replaced:
            next_instances.append(instance)
        self._write_instances(next_instances)
        return instance

    def _write_instances(self, instances: list[InstalledServer]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "instances": [instance.to_record() for instance in instances],
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class InMemorySecretStore:
    def __init__(self):
        self._secrets: dict[tuple[str, str], str] = {}

    def set_secret(self, instance_id: str, target: str, value: str) -> None:
        self._secrets[(instance_id, target)] = value

    def has_secret(self, instance_id: str, target: str) -> bool:
        return (instance_id, target) in self._secrets

    def clear_secret(self, instance_id: str, target: str) -> None:
        self._secrets.pop((instance_id, target), None)

    def resolve_secrets(self, instance_id: str, targets: list[str] | tuple[str, ...] | None = None) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for (stored_instance_id, target), value in self._secrets.items():
            if stored_instance_id != instance_id:
                continue
            if targets is not None and target not in targets:
                continue
            resolved[target] = value
        return resolved


__all__ = [
    "FileCatalogCache",
    "FileInstalledServerStore",
    "InMemorySecretStore",
    "default_pupu_data_dir",
]
