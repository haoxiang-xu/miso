from __future__ import annotations

import inspect
import json
from typing import Any, Callable, get_type_hints

from .models import (
    HistoryPayloadOptimizer,
    ToolParameter,
    _annotation_to_json_schema,
    _escape_control_chars_inside_json_strings,
    _parse_docstring,
)


class Tool:
    def __init__(
        self,
        name: str | Callable[..., Any] = "",
        description: str = "",
        func: Callable[..., Any] | None = None,
        parameters: list[ToolParameter | dict[str, Any]] | None = None,
        observe: bool = False,
        requires_confirmation: bool = False,
        render_component: dict[str, Any] | None = None,
        history_arguments_optimizer: HistoryPayloadOptimizer | None = None,
        history_result_optimizer: HistoryPayloadOptimizer | None = None,
    ):
        if callable(name) and func is None:
            func = name
            name = ""

        self.name = name
        self.description = description
        self.func = func
        self.observe = observe
        self.requires_confirmation = requires_confirmation
        self.render_component = render_component
        self.history_arguments_optimizer = history_arguments_optimizer
        self.history_result_optimizer = history_result_optimizer
        self.parameters = self._construct_parameters(parameters)

        if self.func is not None and not self.parameters:
            self.parameters = self._infer_parameters_from_func(self.func)

        if not self.name and self.func is not None:
            self.name = self.func.__name__

        if not self.description and self.func is not None:
            summary, _ = _parse_docstring(self.func)
            self.description = summary

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.func is None and len(args) == 1 and callable(args[0]) and not kwargs:
            func = args[0]
            return Tool.from_callable(
                func,
                name=self.name or None,
                description=self.description or None,
                parameters=self.parameters or None,
                observe=self.observe,
                requires_confirmation=self.requires_confirmation,
                render_component=self.render_component,
                history_arguments_optimizer=self.history_arguments_optimizer,
                history_result_optimizer=self.history_result_optimizer,
            )

        if self.func is not None:
            return self.func(*args, **kwargs)

        raise TypeError("Tool object is not callable without a wrapped function")

    @classmethod
    def from_callable(
        cls,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: list[ToolParameter | dict[str, Any]] | None = None,
        observe: bool = False,
        requires_confirmation: bool = False,
        render_component: dict[str, Any] | None = None,
        history_arguments_optimizer: HistoryPayloadOptimizer | None = None,
        history_result_optimizer: HistoryPayloadOptimizer | None = None,
    ) -> "Tool":
        summary, _ = _parse_docstring(func)
        return cls(
            name=name or func.__name__,
            description=description or summary,
            func=func,
            parameters=parameters,
            observe=observe,
            requires_confirmation=requires_confirmation,
            render_component=render_component,
            history_arguments_optimizer=history_arguments_optimizer,
            history_result_optimizer=history_result_optimizer,
        )

    def _construct_parameters(
        self,
        parameters: list[ToolParameter | dict[str, Any]] | None,
    ) -> list[ToolParameter]:
        constructed_parameters: list[ToolParameter] = []
        for parameter in parameters or []:
            if isinstance(parameter, ToolParameter):
                constructed_parameters.append(parameter)
            elif isinstance(parameter, dict):
                constructed_parameters.append(
                    ToolParameter(
                        name=parameter.get("name", ""),
                        description=parameter.get("description", ""),
                        type_=parameter.get("type_", "string"),
                        required=parameter.get("required", False),
                        pattern=parameter.get("pattern"),
                        items=parameter.get("items"),
                    )
                )
        return constructed_parameters

    def _infer_parameters_from_func(self, func: Callable[..., Any]) -> list[ToolParameter]:
        inferred: list[ToolParameter] = []
        signature = inspect.signature(func)
        try:
            resolved_hints = get_type_hints(func)
        except Exception:
            resolved_hints = {}
        _, parameter_descriptions = _parse_docstring(func)

        for name, param in signature.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue

            annotation = resolved_hints.get(name, param.annotation)
            if annotation == inspect._empty:
                annotation = str
            schema = _annotation_to_json_schema(annotation)
            type_ = schema.get("type", "string")
            items = schema.get("items") if type_ == "array" else None
            required = param.default == inspect._empty
            inferred.append(
                ToolParameter(
                    name=name,
                    description=parameter_descriptions.get(name, f"Argument {name}"),
                    type_=type_,
                    required=required,
                    items=items,
                )
            )
        return inferred

    def to_json(self) -> dict[str, Any]:
        json_parameters: dict[str, Any] = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

        for parameter in self.parameters:
            json_parameters["properties"][parameter.name] = parameter.to_json()
            if parameter.required:
                json_parameters["required"].append(parameter.name)

        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": json_parameters,
        }

    def execute(self, arguments: dict[str, Any] | str | None) -> dict[str, Any]:
        if self.func is None:
            return {"error": "tool function not implemented (Tool -> execute)"}

        try:
            if arguments is None:
                parsed_arguments: dict[str, Any] = {}
            elif isinstance(arguments, str):
                stripped = arguments.strip()
                if not stripped:
                    parsed_arguments = {}
                else:
                    try:
                        parsed_arguments = json.loads(stripped)
                    except json.JSONDecodeError:
                        repaired = _escape_control_chars_inside_json_strings(stripped)
                        parsed_arguments = json.loads(repaired)
            elif isinstance(arguments, dict):
                parsed_arguments = arguments
            else:
                return {"error": "invalid tool arguments type"}

            result = self.func(**parsed_arguments)
            if isinstance(result, dict):
                return result
            return {"result": result}
        except Exception as exc:
            return {"error": str(exc), "tool": self.name}


__all__ = ["Tool"]
