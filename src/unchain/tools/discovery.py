from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ToolPromptSpec
from .registry import ToolRegistryConfig, ToolkitDescriptor, ToolkitRegistry
from .tool import Tool
from .toolkit import Toolkit


def _normalize_toolkit_ids(values: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values or ():
        value = str(raw).strip()
        if value and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


@dataclass(frozen=True)
class ToolDiscoveryConfig:
    managed_toolkit_ids: tuple[str, ...]
    registry: ToolRegistryConfig

    def __init__(
        self,
        *,
        managed_toolkit_ids: tuple[str, ...] | list[str] | None,
        registry: ToolRegistryConfig | dict[str, Any] | None = None,
    ) -> None:
        normalized = _normalize_toolkit_ids(managed_toolkit_ids)
        if not normalized:
            raise ValueError("tool discovery mode requires at least one managed_toolkit_id")
        object.__setattr__(self, "managed_toolkit_ids", normalized)
        object.__setattr__(self, "registry", ToolRegistryConfig.coerce(registry))

    @classmethod
    def coerce(cls, value: ToolDiscoveryConfig | dict[str, Any] | None) -> ToolDiscoveryConfig | None:
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(**value)
        raise TypeError("tool discovery config must be ToolDiscoveryConfig, dict, or None")


@dataclass(frozen=True)
class DeferredToolRecord:
    handle: str
    toolkit_id: str
    toolkit_name: str
    tool_name: str
    title: str
    description: str
    tags: tuple[str, ...] = ()

    def search_blob(self) -> str:
        return " ".join(
            part
            for part in (
                self.handle,
                self.tool_name,
                self.title,
                self.description,
                self.toolkit_id,
                self.toolkit_name,
                " ".join(self.tags),
            )
            if isinstance(part, str) and part.strip()
        ).lower()

    def to_summary(self) -> dict[str, Any]:
        return {
            "handle": self.handle,
            "tool_name": self.tool_name,
            "toolkit_id": self.toolkit_id,
            "toolkit_name": self.toolkit_name,
            "title": self.title,
            "description": self.description,
        }


class ToolDiscoveryRuntime:
    def __init__(
        self,
        *,
        config: ToolDiscoveryConfig,
        runtime_toolkit: Toolkit,
    ) -> None:
        self.config = config
        self.runtime_toolkit = runtime_toolkit
        self.registry = ToolkitRegistry(config.registry)
        self._managed_descriptors: dict[str, ToolkitDescriptor] = {}
        self._records_by_handle: dict[str, DeferredToolRecord] = {}
        self._cached_toolkits: dict[str, Toolkit] = {}
        self._loaded_handles: list[str] = []

        for toolkit_id in self.config.managed_toolkit_ids:
            descriptor = self.registry.get(toolkit_id)
            if descriptor is None:
                raise ValueError(f"unknown managed toolkit id: {toolkit_id}")
            self._managed_descriptors[toolkit_id] = descriptor
            for tool_descriptor in descriptor.sorted_tools():
                if descriptor.hidden or tool_descriptor.hidden:
                    continue
                handle = self._make_handle(toolkit_id, tool_descriptor.name)
                self._records_by_handle[handle] = DeferredToolRecord(
                    handle=handle,
                    toolkit_id=toolkit_id,
                    toolkit_name=descriptor.name,
                    tool_name=tool_descriptor.name,
                    title=tool_descriptor.title,
                    description=tool_descriptor.description,
                    tags=descriptor.tags,
                )

    def build_tools(self) -> tuple[Tool, Tool, Tool]:
        return (
            Tool.from_callable(
                self.tool_search,
                name="tool_search",
                description="Search deferred tools that can be loaded on demand.",
                prompt_spec=ToolPromptSpec(
                    purpose="Find deferred tools from managed toolkit catalogs before loading them.",
                    when_to_use=(
                        "You need a capability that is not currently available in the active tool list.",
                        "You know the kind of tool you need but not its exact handle.",
                    ),
                    when_not_to_use=("A matching active tool is already available and callable now.",),
                    examples=(
                        'tool_search(query="github issue", max_results=5)',
                        'tool_search(query="browser tabs")',
                    ),
                    advanced_tips=("Search results return stable handles in the form toolkit_id:tool_name.",),
                ),
            ),
            Tool.from_callable(
                self.tool_load,
                name="tool_load",
                description="Load one or more deferred tools into the active tool list.",
                prompt_spec=ToolPromptSpec(
                    purpose="Activate deferred tools returned by tool_search so they become callable next turn.",
                    when_to_use=("You already know the exact deferred tool handle you want to activate.",),
                    when_not_to_use=("You still need to discover candidate tools first.",),
                    examples=(
                        'tool_load(handles=["demo:echo"])',
                        'tool_load(handles=["browser:open_tab", "browser:list_tabs"])',
                    ),
                    advanced_tips=("Loaded tools become part of the active tool list for subsequent turns.",),
                ),
            ),
            Tool.from_callable(
                self.tool_list_loaded,
                name="tool_list_loaded",
                description="List deferred tools that have already been loaded into the active tool list.",
                prompt_spec=ToolPromptSpec(
                    purpose="Inspect which deferred tools are already active in the current run.",
                    when_to_use=("You are unsure whether a deferred tool was already loaded earlier in the run.",),
                    examples=("tool_list_loaded()",),
                ),
            ),
        )

    def _make_handle(self, toolkit_id: str, tool_name: str) -> str:
        return f"{toolkit_id}:{tool_name}"

    def _remaining_records(self) -> list[DeferredToolRecord]:
        loaded = set(self._loaded_handles)
        return [record for handle, record in self._records_by_handle.items() if handle not in loaded]

    def _search_records(self, query: str, max_results: int) -> list[DeferredToolRecord]:
        query_text = str(query or "").strip().lower()
        if not query_text:
            return self._remaining_records()[:max_results]

        direct = self._records_by_handle.get(query_text)
        if direct is not None and direct.handle not in self._loaded_handles:
            return [direct]

        terms = [term for term in query_text.split() if term]
        scored: list[tuple[int, DeferredToolRecord]] = []
        for record in self._remaining_records():
            blob = record.search_blob()
            score = 0
            for term in terms:
                if record.handle.lower() == term or record.tool_name.lower() == term:
                    score += 20
                elif term in record.handle.lower():
                    score += 10
                elif term in record.tool_name.lower():
                    score += 8
                elif term in record.title.lower():
                    score += 6
                elif term in record.toolkit_id.lower() or term in record.toolkit_name.lower():
                    score += 4
                elif term in blob:
                    score += 2
            if score > 0:
                scored.append((score, record))

        scored.sort(key=lambda item: (-item[0], item[1].handle))
        return [record for _, record in scored[:max_results]]

    def _cached_toolkit(self, toolkit_id: str) -> Toolkit:
        cached = self._cached_toolkits.get(toolkit_id)
        if cached is not None:
            return cached
        cached = self.registry.instantiate_toolkit(toolkit_id)
        self._cached_toolkits[toolkit_id] = cached
        return cached

    def _tool_name_conflict(self, tool_name: str) -> bool:
        return tool_name in self.runtime_toolkit.tools

    def tool_search(self, query: str, max_results: int = 5) -> dict[str, Any]:
        resolved_max = max(1, min(int(max_results or 5), 50))
        matches = [record.to_summary() for record in self._search_records(query, resolved_max)]
        return {
            "matches": matches,
            "query": str(query or ""),
            "total_matches": len(matches),
            "total_deferred_tools": len(self._remaining_records()),
        }

    def tool_load(self, handles: list[str]) -> dict[str, Any]:
        if not isinstance(handles, list):
            handles = [str(handles or "")]

        loaded: list[dict[str, Any]] = []
        already_loaded: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []

        for raw_handle in handles:
            handle = str(raw_handle or "").strip()
            if not handle:
                continue
            record = self._records_by_handle.get(handle)
            if record is None:
                failed.append({"handle": handle, "error": "unknown deferred tool handle"})
                continue
            if handle in self._loaded_handles:
                already_loaded.append(record.to_summary())
                continue
            if self._tool_name_conflict(record.tool_name):
                failed.append(
                    {
                        "handle": handle,
                        "tool_name": record.tool_name,
                        "toolkit_id": record.toolkit_id,
                        "error": f"tool name conflict: {record.tool_name}",
                    }
                )
                continue
            source_toolkit = self._cached_toolkit(record.toolkit_id)
            source_tool = source_toolkit.get(record.tool_name)
            if source_tool is None:
                failed.append(
                    {
                        "handle": handle,
                        "tool_name": record.tool_name,
                        "toolkit_id": record.toolkit_id,
                        "error": "tool missing from instantiated toolkit",
                    }
                )
                continue
            self.runtime_toolkit.register(source_tool)
            self._loaded_handles.append(handle)
            loaded.append(record.to_summary())

        return {
            "loaded": loaded,
            "already_loaded": already_loaded,
            "failed": failed,
        }

    def tool_list_loaded(self) -> dict[str, Any]:
        return {
            "loaded": [
                self._records_by_handle[handle].to_summary()
                for handle in self._loaded_handles
                if handle in self._records_by_handle
            ]
        }

    def shutdown(self) -> None:
        for toolkit_obj in self._cached_toolkits.values():
            toolkit_obj.shutdown()
        self._cached_toolkits.clear()


__all__ = [
    "DeferredToolRecord",
    "ToolDiscoveryConfig",
    "ToolDiscoveryRuntime",
]
