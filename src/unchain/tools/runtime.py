from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..interaction.durable import InteractionIntegrityError
from ..kernel.delta import SuspendSignal
from ..kernel.types import ToolCall


@dataclass(frozen=True)
class ToolRuntimeOutcome:
    handled: bool = True
    tool_result: dict[str, Any] | None = None
    result_messages: list[dict[str, Any]] = field(default_factory=list)
    state_updates: dict[str, Any] = field(default_factory=dict)
    should_observe: bool = False
    suspend_override: SuspendSignal | None = None


@runtime_checkable
class ToolRuntimePlugin(Protocol):
    def can_handle(self, *, tool_call: ToolCall, context: Any) -> bool:
        ...

    def execute(self, *, tool_call: ToolCall, context: Any) -> ToolRuntimeOutcome:
        ...


def snapshot_durable_tool_exposure_plan(
    plugins: list[Any] | None,
) -> dict[str, Any] | None:
    snapshots: list[dict[str, Any]] = []
    for plugin in plugins or []:
        snapshotter = getattr(plugin, "durable_exposure_plan", None)
        if not callable(snapshotter):
            continue
        snapshot = snapshotter()
        if not isinstance(snapshot, dict):
            raise InteractionIntegrityError(
                "tool runtime plugin returned an invalid durable exposure plan"
            )
        snapshots.append(copy.deepcopy(snapshot))
    if not snapshots:
        return None
    first = snapshots[0]
    if any(snapshot != first for snapshot in snapshots[1:]):
        raise InteractionIntegrityError(
            "tool runtime plugins returned conflicting durable exposure plans"
        )
    return first


def snapshot_durable_tool_runtime_route(
    plugins: list[Any] | None,
    *,
    tool_call: ToolCall,
    context: Any,
) -> dict[str, Any] | None:
    """Snapshot durable handlers that can execute one approved tool call.

    The snapshot is embedded in the durable approval subject. A cold resume
    must rebuild the same handler/configuration route before its receipt can be
    applied; silently falling back to another executor is an integrity error.
    """

    matching: list[tuple[Any, Any]] = []
    for plugin in plugins or []:
        can_handle = getattr(plugin, "can_handle", None)
        if not callable(can_handle):
            continue
        if not can_handle(tool_call=tool_call, context=context):
            continue
        matching.append(
            (plugin, getattr(plugin, "durable_runtime_manifest", None))
        )

    if not matching:
        return None

    handlers: list[dict[str, Any]] = []
    for plugin, manifest_factory in matching:
        if not callable(manifest_factory):
            plugin_type = type(plugin)
            raise InteractionIntegrityError(
                "tool runtime route cannot be durably bound because matching "
                f"plugin {plugin_type.__module__}.{plugin_type.__qualname__} "
                "has no durable runtime manifest"
            )
        manifest = manifest_factory(tool_call=tool_call, context=context)
        if not isinstance(manifest, dict):
            raise InteractionIntegrityError(
                "tool runtime plugin returned an invalid durable route manifest"
            )
        plugin_type = type(plugin)
        handlers.append(
            {
                "plugin": f"{plugin_type.__module__}.{plugin_type.__qualname__}",
                "manifest": copy.deepcopy(manifest),
            }
        )
        if manifest.get("terminal_handler") is True:
            break
    if not handlers:
        return None
    return {
        "schema_version": 1,
        "handlers": handlers,
    }


def _copy_tool_result_snapshot(tool_result: dict[str, Any]) -> dict[str, Any]:
    try:
        return copy.deepcopy(tool_result)
    except Exception:
        try:
            return json.loads(json.dumps(tool_result, default=str, ensure_ascii=False))
        except Exception:
            return {"result": str(tool_result)}


def run_tool_runtime_plugins(
    plugins: list[ToolRuntimePlugin],
    *,
    tool_call: ToolCall,
    context: Any,
    execution_guard: Any = None,
) -> ToolRuntimeOutcome | None:
    for plugin in plugins:
        if execution_guard is not None:
            execution_guard.renew()
        if not plugin.can_handle(tool_call=tool_call, context=context):
            continue
        manifest_factory = getattr(
            plugin,
            "durable_runtime_manifest",
            None,
        )
        terminal_handler = False
        if callable(manifest_factory):
            manifest = manifest_factory(
                tool_call=tool_call,
                context=context,
            )
            if not isinstance(manifest, dict):
                raise InteractionIntegrityError(
                    "tool runtime plugin returned an invalid durable route manifest"
                )
            terminal_handler = manifest.get("terminal_handler") is True
        if execution_guard is not None:
            execution_guard.renew()
        outcome = plugin.execute(tool_call=tool_call, context=context)
        if execution_guard is not None:
            execution_guard.assert_active()
        if isinstance(outcome, ToolRuntimeOutcome) and outcome.handled:
            return ToolRuntimeOutcome(
                handled=True,
                tool_result=_copy_tool_result_snapshot(outcome.tool_result) if isinstance(outcome.tool_result, dict) else outcome.tool_result,
                result_messages=copy.deepcopy(outcome.result_messages),
                state_updates=copy.deepcopy(outcome.state_updates),
                should_observe=bool(outcome.should_observe),
                suspend_override=outcome.suspend_override,
            )
        if terminal_handler:
            raise InteractionIntegrityError(
                "durable terminal tool runtime handler declined its bound route"
            )
    return None
