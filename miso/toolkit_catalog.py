from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from .tool import toolkit
from .tool_registry import ToolRegistryConfig, ToolkitDescriptor, ToolkitRegistry

TOOLKIT_LIST_TOOL_NAME = "toolkit_list"
TOOLKIT_DESCRIBE_TOOL_NAME = "toolkit_describe"
TOOLKIT_ACTIVATE_TOOL_NAME = "toolkit_activate"
TOOLKIT_DEACTIVATE_TOOL_NAME = "toolkit_deactivate"
TOOLKIT_LIST_ACTIVE_TOOL_NAME = "toolkit_list_active"
CATALOG_TOOL_NAMES = (
    TOOLKIT_LIST_TOOL_NAME,
    TOOLKIT_DESCRIBE_TOOL_NAME,
    TOOLKIT_ACTIVATE_TOOL_NAME,
    TOOLKIT_DEACTIVATE_TOOL_NAME,
    TOOLKIT_LIST_ACTIVE_TOOL_NAME,
)


def _normalize_toolkit_ids(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values or ():
        value = str(raw).strip()
        if value and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


@dataclass
class ToolkitCatalogConfig:
    managed_toolkit_ids: tuple[str, ...]
    always_active_toolkit_ids: tuple[str, ...]
    registry: ToolRegistryConfig
    readme_max_chars: int

    def __init__(
        self,
        *,
        managed_toolkit_ids: tuple[str, ...] | list[str] | None,
        always_active_toolkit_ids: tuple[str, ...] | list[str] | None = None,
        registry: ToolRegistryConfig | dict[str, Any] | None = None,
        readme_max_chars: int = 8000,
    ):
        normalized_managed = _normalize_toolkit_ids(managed_toolkit_ids)
        if not normalized_managed:
            raise ValueError("toolkit catalog mode requires at least one managed_toolkit_id")

        normalized_always_active = _normalize_toolkit_ids(always_active_toolkit_ids)
        missing_always_active = sorted(set(normalized_always_active) - set(normalized_managed))
        if missing_always_active:
            raise ValueError(
                "toolkit catalog always_active_toolkit_ids must be a subset of managed_toolkit_ids: "
                + ", ".join(missing_always_active)
            )

        self.managed_toolkit_ids = normalized_managed
        self.always_active_toolkit_ids = normalized_always_active
        self.registry = ToolRegistryConfig.coerce(registry)
        self.readme_max_chars = max(0, int(readme_max_chars))

    @classmethod
    def coerce(cls, value: ToolkitCatalogConfig | dict[str, Any] | None) -> ToolkitCatalogConfig | None:
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**value)
        raise TypeError("toolkit_catalog_config must be ToolkitCatalogConfig, dict, or None")


class ToolkitCatalogRuntime(toolkit):
    def __init__(
        self,
        *,
        config: ToolkitCatalogConfig,
        eager_toolkits: list[toolkit],
    ):
        super().__init__()
        self.config = config
        self.eager_toolkits = list(eager_toolkits)
        self.registry = ToolkitRegistry(config.registry)
        self._managed_descriptors: dict[str, ToolkitDescriptor] = {}
        self._active_toolkit_ids: list[str] = []
        self._cached_toolkits: dict[str, toolkit] = {}
        self._state_token: str | None = None
        self._shutdown_called = False

        for toolkit_id in self.config.managed_toolkit_ids:
            descriptor = self.registry.get(toolkit_id)
            if descriptor is None:
                raise ValueError(f"unknown managed toolkit id: {toolkit_id}")
            self._managed_descriptors[toolkit_id] = descriptor

        eager_tool_names = self._collect_tool_names(self.eager_toolkits)
        collisions = sorted(eager_tool_names & set(CATALOG_TOOL_NAMES))
        if collisions:
            raise ValueError(
                "toolkit catalog reserved tool names collide with eager tools: "
                + ", ".join(collisions)
            )

        self._register_catalog_tools()

        for toolkit_id in self.config.always_active_toolkit_ids:
            activation = self._activate_toolkit(toolkit_id)
            if not activation.get("ok"):
                raise ValueError(activation.get("error", f"failed to activate toolkit '{toolkit_id}'"))

    def _register_catalog_tools(self) -> None:
        self.register_many(
            self.toolkit_list,
            self.toolkit_describe,
            self.toolkit_activate,
            self.toolkit_deactivate,
            self.toolkit_list_active,
        )

    def _collect_tool_names(self, toolkits: list[toolkit]) -> set[str]:
        names: set[str] = set()
        for toolkit_obj in toolkits:
            names.update(toolkit_obj.tools.keys())
        return names

    def _descriptor(self, toolkit_id: str) -> ToolkitDescriptor | None:
        return self._managed_descriptors.get(str(toolkit_id).strip())

    def _cached_toolkit(self, toolkit_id: str) -> toolkit:
        cached = self._cached_toolkits.get(toolkit_id)
        if cached is not None:
            return cached
        cached = self.registry.instantiate_toolkit(toolkit_id)
        self._cached_toolkits[toolkit_id] = cached
        return cached

    def _active_toolkits(self) -> list[toolkit]:
        return [
            self._cached_toolkits[toolkit_id]
            for toolkit_id in self._active_toolkit_ids
            if toolkit_id in self._cached_toolkits
        ]

    def visible_toolkits(self) -> list[toolkit]:
        return list(self.eager_toolkits) + [self] + self._active_toolkits()

    def active_toolkit_ids(self) -> list[str]:
        return list(self._active_toolkit_ids)

    def _toolkit_summary(self, descriptor: ToolkitDescriptor) -> dict[str, Any]:
        return {
            "id": descriptor.id,
            "name": descriptor.name,
            "description": descriptor.description,
            "tags": list(descriptor.tags),
            "tool_count": len(descriptor.tools),
            "active": descriptor.id in self._active_toolkit_ids,
            "always_active": descriptor.id in self.config.always_active_toolkit_ids,
            "hidden": descriptor.hidden,
        }

    def _truncate_readme(self, markdown: str) -> tuple[str, bool]:
        if len(markdown) <= self.config.readme_max_chars:
            return markdown, False
        return markdown[: self.config.readme_max_chars], True

    def _tool_conflicts(self, toolkit_id: str, candidate: toolkit) -> list[str]:
        reserved_names = self._collect_tool_names(self.eager_toolkits) | set(self.tools.keys())
        active_names: set[str] = set()
        for active_id in self._active_toolkit_ids:
            if active_id == toolkit_id:
                continue
            active_names.update(self._cached_toolkits[active_id].tools.keys())
        conflicts = reserved_names | active_names
        return sorted(conflicts & set(candidate.tools.keys()))

    def _activate_toolkit(self, toolkit_id: str) -> dict[str, Any]:
        descriptor = self._descriptor(toolkit_id)
        if descriptor is None:
            return {"ok": False, "toolkit_id": toolkit_id, "error": f"unknown managed toolkit: {toolkit_id}"}

        if toolkit_id in self._active_toolkit_ids:
            cached = self._cached_toolkit(toolkit_id)
            return {
                "ok": True,
                "toolkit_id": toolkit_id,
                "already_active": True,
                "always_active": toolkit_id in self.config.always_active_toolkit_ids,
                "tool_count": len(cached.tools),
                "tool_names": sorted(cached.tools.keys()),
            }

        cached = self._cached_toolkit(toolkit_id)
        conflicts = self._tool_conflicts(toolkit_id, cached)
        if conflicts:
            return {
                "ok": False,
                "toolkit_id": toolkit_id,
                "error": (
                    f"toolkit '{toolkit_id}' cannot be activated because these tool names already exist: "
                    + ", ".join(conflicts)
                ),
                "conflicts": conflicts,
            }

        self._active_toolkit_ids.append(toolkit_id)
        return {
            "ok": True,
            "toolkit_id": toolkit_id,
            "already_active": False,
            "always_active": toolkit_id in self.config.always_active_toolkit_ids,
            "tool_count": len(cached.tools),
            "tool_names": sorted(cached.tools.keys()),
        }

    def toolkit_list(self) -> dict[str, Any]:
        return {
            "toolkits": [
                self._toolkit_summary(descriptor)
                for descriptor in self._managed_descriptors.values()
                if not descriptor.hidden
            ]
        }

    def toolkit_describe(self, toolkit_id: str, tool_name: str | None = None) -> dict[str, Any]:
        descriptor = self._descriptor(toolkit_id)
        if descriptor is None:
            return {"error": f"unknown managed toolkit: {toolkit_id}", "toolkit_id": toolkit_id}

        if tool_name is None:
            metadata = descriptor.to_metadata(include_tools=True)
            readme_markdown, truncated = self._truncate_readme(metadata.get("readme_markdown", ""))
            metadata["readme_markdown"] = readme_markdown
            metadata["readme_truncated"] = truncated
            metadata["active"] = descriptor.id in self._active_toolkit_ids
            metadata["always_active"] = descriptor.id in self.config.always_active_toolkit_ids
            return metadata

        tool_descriptor = descriptor.tools.get(tool_name)
        if tool_descriptor is None:
            return {
                "error": f"unknown tool '{tool_name}' for toolkit '{toolkit_id}'",
                "toolkit_id": toolkit_id,
                "tool_name": tool_name,
            }

        return {
            "toolkit": {
                **descriptor.to_summary(include_tools=False),
                "active": descriptor.id in self._active_toolkit_ids,
                "always_active": descriptor.id in self.config.always_active_toolkit_ids,
            },
            "tool": tool_descriptor.to_summary(),
        }

    def toolkit_activate(self, toolkit_id: str) -> dict[str, Any]:
        return self._activate_toolkit(toolkit_id)

    def toolkit_deactivate(self, toolkit_id: str) -> dict[str, Any]:
        descriptor = self._descriptor(toolkit_id)
        if descriptor is None:
            return {"ok": False, "toolkit_id": toolkit_id, "error": f"unknown managed toolkit: {toolkit_id}"}

        if toolkit_id in self.config.always_active_toolkit_ids:
            return {
                "ok": False,
                "toolkit_id": toolkit_id,
                "error": f"toolkit '{toolkit_id}' is always_active and cannot be deactivated",
            }

        if toolkit_id not in self._active_toolkit_ids:
            cached = self._cached_toolkits.get(toolkit_id)
            return {
                "ok": True,
                "toolkit_id": toolkit_id,
                "already_inactive": True,
                "tool_count": len(cached.tools) if cached is not None else len(descriptor.tools),
            }

        self._active_toolkit_ids = [active_id for active_id in self._active_toolkit_ids if active_id != toolkit_id]
        cached = self._cached_toolkits.get(toolkit_id)
        return {
            "ok": True,
            "toolkit_id": toolkit_id,
            "already_inactive": False,
            "tool_count": len(cached.tools) if cached is not None else len(descriptor.tools),
        }

    def toolkit_list_active(self) -> dict[str, Any]:
        return {
            "active_toolkits": [
                {
                    "toolkit_id": toolkit_id,
                    "always_active": toolkit_id in self.config.always_active_toolkit_ids,
                }
                for toolkit_id in self._active_toolkit_ids
            ]
        }

    def build_continuation_state(self) -> dict[str, Any]:
        if not self._state_token:
            self._state_token = f"toolkit_catalog_{uuid.uuid4().hex}"
        return {
            "state_token": self._state_token,
            "active_toolkit_ids": self.active_toolkit_ids(),
            "always_active_toolkit_ids": list(self.config.always_active_toolkit_ids),
            "managed_toolkit_ids": list(self.config.managed_toolkit_ids),
        }

    def shutdown(self) -> None:
        if self._shutdown_called:
            return
        self._shutdown_called = True
        for toolkit_obj in self._cached_toolkits.values():
            toolkit_obj.shutdown()

    def to_summary(self) -> dict[str, Any]:
        return {
            "managed_toolkit_ids": list(self.config.managed_toolkit_ids),
            "always_active_toolkit_ids": list(self.config.always_active_toolkit_ids),
            "active_toolkit_ids": self.active_toolkit_ids(),
        }


def build_visible_toolkits(
    *,
    eager_toolkits: list[toolkit],
    catalog_runtime: ToolkitCatalogRuntime | None,
) -> list[toolkit]:
    if catalog_runtime is None:
        return list(eager_toolkits)
    return catalog_runtime.visible_toolkits()


def extract_toolkit_catalog_token(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    state_token = payload.get("state_token")
    if not isinstance(state_token, str) or not state_token.strip():
        return None
    return state_token.strip()


__all__ = [
    "CATALOG_TOOL_NAMES",
    "TOOLKIT_ACTIVATE_TOOL_NAME",
    "TOOLKIT_DEACTIVATE_TOOL_NAME",
    "TOOLKIT_DESCRIBE_TOOL_NAME",
    "TOOLKIT_LIST_ACTIVE_TOOL_NAME",
    "TOOLKIT_LIST_TOOL_NAME",
    "ToolkitCatalogConfig",
    "ToolkitCatalogRuntime",
    "build_visible_toolkits",
    "extract_toolkit_catalog_token",
]
