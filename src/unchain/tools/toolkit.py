from __future__ import annotations

from typing import Any, Callable

from .models import HistoryPayloadOptimizer, ToolParameter
from .tool import Tool


class Toolkit:
    def __init__(self, tools: dict[str, Tool] | None = None):
        self.tools: dict[str, Tool] = {}
        for tool_name, tool_obj in (tools or {}).items():
            if isinstance(tool_obj, Tool):
                self.tools[tool_name] = tool_obj

    def register(
        self,
        tool_obj: Tool | Callable[..., Any],
        *,
        observe: bool | None = None,
        requires_confirmation: bool | None = None,
        name: str | None = None,
        description: str | None = None,
        parameters: list[ToolParameter | dict[str, Any]] | None = None,
        history_arguments_optimizer: HistoryPayloadOptimizer | None = None,
        history_result_optimizer: HistoryPayloadOptimizer | None = None,
    ) -> Tool:
        if isinstance(tool_obj, Tool):
            if name is not None:
                tool_obj.name = name
            if description is not None:
                tool_obj.description = description
            if parameters is not None:
                tool_obj.parameters = tool_obj._construct_parameters(parameters)
            if observe is not None:
                tool_obj.observe = observe
            if requires_confirmation is not None:
                tool_obj.requires_confirmation = requires_confirmation
            if history_arguments_optimizer is not None:
                tool_obj.history_arguments_optimizer = history_arguments_optimizer
            if history_result_optimizer is not None:
                tool_obj.history_result_optimizer = history_result_optimizer
            self.tools[tool_obj.name] = tool_obj
            return tool_obj

        if callable(tool_obj):
            wrapped = Tool.from_callable(
                tool_obj,
                name=name,
                description=description,
                parameters=parameters,
                observe=bool(observe),
                requires_confirmation=bool(requires_confirmation),
                history_arguments_optimizer=history_arguments_optimizer,
                history_result_optimizer=history_result_optimizer,
            )
            self.tools[wrapped.name] = wrapped
            return wrapped

        raise ValueError("invalid tool passed to register")

    def register_many(self, *tool_objs: Tool | Callable[..., Any]) -> list[Tool]:
        registered: list[Tool] = []
        for tool_obj in tool_objs:
            registered.append(self.register(tool_obj))
        return registered

    def tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        observe: bool = False,
        requires_confirmation: bool = False,
        name: str | None = None,
        description: str | None = None,
        parameters: list[ToolParameter | dict[str, Any]] | None = None,
        history_arguments_optimizer: HistoryPayloadOptimizer | None = None,
        history_result_optimizer: HistoryPayloadOptimizer | None = None,
    ):
        if func is not None:
            return self.register(
                func,
                observe=observe,
                requires_confirmation=requires_confirmation,
                name=name,
                description=description,
                parameters=parameters,
                history_arguments_optimizer=history_arguments_optimizer,
                history_result_optimizer=history_result_optimizer,
            )

        def decorator(inner: Callable[..., Any]) -> Tool:
            return self.register(
                inner,
                observe=observe,
                requires_confirmation=requires_confirmation,
                name=name,
                description=description,
                parameters=parameters,
                history_arguments_optimizer=history_arguments_optimizer,
                history_result_optimizer=history_result_optimizer,
            )

        return decorator

    def get(self, function_name: str) -> Tool | None:
        return self.tools.get(function_name)

    def execute(self, function_name: str, arguments: dict[str, Any] | str | None) -> dict[str, Any]:
        tool_obj = self.get(function_name)
        if tool_obj is None:
            return {"error": f"tool not found: {function_name}", "tool": function_name}
        return tool_obj.execute(arguments)

    def to_json(self) -> list[dict[str, Any]]:
        return [tool_obj.to_json() for tool_obj in self.tools.values()]

    def shutdown(self) -> None:
        return None


__all__ = ["Toolkit"]
