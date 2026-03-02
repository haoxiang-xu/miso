from __future__ import annotations

import inspect
import json
import re
import types
from dataclasses import dataclass
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints

_UNION_TYPE = getattr(types, "UnionType", None)


def _escape_control_chars_inside_json_strings(raw: str) -> str:
    """Escape raw control chars that appear inside JSON string literals.

    Some model tool-call arguments occasionally include literal newlines or tabs
    inside quoted JSON string values, which breaks ``json.loads``. This helper
    rewrites those characters to their escaped forms while preserving valid JSON.
    """
    chars: list[str] = []
    in_string = False
    escaped = False

    for ch in raw:
        if in_string:
            if escaped:
                chars.append(ch)
                escaped = False
                continue
            if ch == "\\":
                chars.append(ch)
                escaped = True
                continue
            if ch == "\"":
                chars.append(ch)
                in_string = False
                continue
            if ch == "\n":
                chars.append("\\n")
                continue
            if ch == "\r":
                chars.append("\\r")
                continue
            if ch == "\t":
                chars.append("\\t")
                continue
            chars.append(ch)
            continue

        chars.append(ch)
        if ch == "\"":
            in_string = True
            escaped = False

    return "".join(chars)

def _annotation_to_json_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin in (list, tuple, set, frozenset):
        return "array"
    if origin in (dict,):
        return "object"
    union_origins = (Union,) if _UNION_TYPE is None else (Union, _UNION_TYPE)
    if origin in union_origins:
        union_args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(union_args) == 1:
            return _annotation_to_json_type(union_args[0])
        return "string"
    if origin is not None:
        origin_args = get_args(annotation)
        if origin_args:
            return _annotation_to_json_type(origin)

    if annotation in (int,):
        return "integer"
    if annotation in (float,):
        return "number"
    if annotation in (bool,):
        return "boolean"
    if annotation in (list, tuple, set):
        return "array"
    if annotation in (dict,):
        return "object"
    return "string"

def _annotation_to_json_schema(annotation: Any) -> dict[str, Any]:
    origin = get_origin(annotation)
    if origin in (list, tuple, set, frozenset):
        origin_args = [arg for arg in get_args(annotation) if arg is not Ellipsis]
        item_schema: dict[str, Any] = {"type": "string"}
        if origin_args:
            item_schema = _annotation_to_json_schema(origin_args[0])
        if "type" not in item_schema:
            item_schema = {"type": "string"}
        return {"type": "array", "items": item_schema}

    return {"type": _annotation_to_json_type(annotation)}

def _parse_docstring(func: Callable[..., Any]) -> tuple[str, dict[str, str]]:
    doc = inspect.getdoc(func) or ""
    if not doc:
        return "", {}

    lines = doc.splitlines()
    summary = lines[0].strip()
    parameter_descriptions: dict[str, str] = {}

    # reStructuredText style: :param name: description
    for line in lines:
        match = re.match(r"^\s*:param\s+([A-Za-z_]\w*)\s*:\s*(.+)$", line)
        if match:
            parameter_descriptions[match.group(1)] = match.group(2).strip()

    # Google style:
    # Args:
    #     name: description
    in_args_block = False
    current_parameter: str | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped in {"Args:", "Arguments:", "Parameters:"}:
            in_args_block = True
            current_parameter = None
            continue

        if not in_args_block:
            continue

        if not stripped:
            current_parameter = None
            continue

        if re.match(r"^[A-Z][A-Za-z ]+:$", stripped):
            in_args_block = False
            current_parameter = None
            continue

        parameter_match = re.match(
            r"^([A-Za-z_]\w*)(?:\s*\([^)]+\))?\s*:\s*(.+)$",
            stripped,
        )
        if parameter_match:
            current_parameter = parameter_match.group(1)
            parameter_descriptions[current_parameter] = parameter_match.group(2).strip()
            continue

        if current_parameter and line.startswith((" ", "\t")):
            parameter_descriptions[current_parameter] = (
                f"{parameter_descriptions[current_parameter]} {stripped}".strip()
            )
        else:
            current_parameter = None

    return summary, parameter_descriptions

@dataclass
class tool_parameter:
    name: str
    description: str
    type_: str
    required: bool = False
    pattern: str | None = None
    items: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        json_parameter: dict[str, Any] = {
            "type": self.type_,
            "description": self.description,
        }
        if self.pattern is not None:
            json_parameter["pattern"] = self.pattern
        if self.items is not None:
            json_parameter["items"] = self.items
        elif self.type_ == "array":
            # OpenAI function schema requires array "items".
            json_parameter["items"] = {"type": "string"}
        return json_parameter

class tool:
    def __init__(
        self,
        name: str | Callable[..., Any] = "",
        description: str = "",
        func: Callable[..., Any] | None = None,
        parameters: list[tool_parameter | dict[str, Any]] | None = None,
        observe: bool = False,
        requires_confirmation: bool = False,
    ):
        if callable(name) and func is None:
            func = name
            name = ""

        self.name = name
        self.description = description
        self.func = func
        self.observe = observe
        self.requires_confirmation = requires_confirmation
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
            return tool.from_callable(
                func,
                name=self.name or None,
                description=self.description or None,
                parameters=self.parameters or None,
                observe=self.observe,
                requires_confirmation=self.requires_confirmation,
            )

        if self.func is not None:
            return self.func(*args, **kwargs)

        raise TypeError("tool object is not callable without a wrapped function")

    @classmethod
    def from_callable(
        cls,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: list[tool_parameter | dict[str, Any]] | None = None,
        observe: bool = False,
        requires_confirmation: bool = False,
    ) -> "tool":
        summary, _ = _parse_docstring(func)
        return cls(
            name=name or func.__name__,
            description=description or summary,
            func=func,
            parameters=parameters,
            observe=observe,
            requires_confirmation=requires_confirmation,
        )

    def _construct_parameters(
        self,
        parameters: list[tool_parameter | dict[str, Any]] | None,
    ) -> list[tool_parameter]:
        constructed_parameters: list[tool_parameter] = []
        for parameter in parameters or []:
            if isinstance(parameter, tool_parameter):
                constructed_parameters.append(parameter)
            elif isinstance(parameter, dict):
                constructed_parameters.append(
                    tool_parameter(
                        name=parameter.get("name", ""),
                        description=parameter.get("description", ""),
                        type_=parameter.get("type_", "string"),
                        required=parameter.get("required", False),
                        pattern=parameter.get("pattern"),
                        items=parameter.get("items"),
                    )
                )
        return constructed_parameters

    def _infer_parameters_from_func(self, func: Callable[..., Any]) -> list[tool_parameter]:
        inferred: list[tool_parameter] = []
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
                tool_parameter(
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
            return {"error": "tool function not implemented (tool --> execute)"}

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

def tool_decorator(
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: list[tool_parameter | dict[str, Any]] | None = None,
    observe: bool = False,
    requires_confirmation: bool = False,
):
    def decorator(func: Callable[..., Any]) -> tool:
        return tool.from_callable(
            func,
            name=name,
            description=description,
            parameters=parameters,
            observe=observe,
            requires_confirmation=requires_confirmation,
        )

    return decorator

class toolkit:
    def __init__(self, tools: dict[str, tool] | None = None):
        self.tools: dict[str, tool] = {}
        for tool_name, tool_obj in (tools or {}).items():
            if isinstance(tool_obj, tool):
                self.tools[tool_name] = tool_obj

    def register(
        self,
        tool_obj: tool | Callable[..., Any],
        *,
        observe: bool | None = None,
        requires_confirmation: bool | None = None,
        name: str | None = None,
        description: str | None = None,
        parameters: list[tool_parameter | dict[str, Any]] | None = None,
    ) -> tool:
        if isinstance(tool_obj, tool):
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
            self.tools[tool_obj.name] = tool_obj
            return tool_obj

        if callable(tool_obj):
            wrapped = tool.from_callable(
                tool_obj,
                name=name,
                description=description,
                parameters=parameters,
                observe=bool(observe),
                requires_confirmation=bool(requires_confirmation),
            )
            self.tools[wrapped.name] = wrapped
            return wrapped

        raise ValueError("invalid tool passed to register")

    def register_many(self, *tool_objs: tool | Callable[..., Any]) -> list[tool]:
        registered: list[tool] = []
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
        parameters: list[tool_parameter | dict[str, Any]] | None = None,
    ):
        if func is not None:
            return self.register(
                func,
                observe=observe,
                requires_confirmation=requires_confirmation,
                name=name,
                description=description,
                parameters=parameters,
            )

        def decorator(inner: Callable[..., Any]) -> tool:
            return self.register(
                inner,
                observe=observe,
                requires_confirmation=requires_confirmation,
                name=name,
                description=description,
                parameters=parameters,
            )

        return decorator

    def get(self, function_name: str) -> tool | None:
        return self.tools.get(function_name)

    def execute(self, function_name: str, arguments: dict[str, Any] | str | None) -> dict[str, Any]:
        tool_obj = self.get(function_name)
        if tool_obj is None:
            return {"error": f"tool not found: {function_name}", "tool": function_name}
        return tool_obj.execute(arguments)

    def to_json(self) -> list[dict[str, Any]]:
        return [tool_obj.to_json() for tool_obj in self.tools.values()]

# ── confirmation callback data structures ──────────────────────────────

@dataclass
class ToolConfirmationRequest:
    """Sent to the ``on_tool_confirm`` callback before a tool that has
    ``requires_confirmation=True`` is executed.

    The callback should inspect this object and return a
    :class:`ToolConfirmationResponse` (or a plain ``dict`` / ``bool``).
    """

    tool_name: str
    call_id: str
    arguments: dict[str, Any]
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "tool_confirmation_request",
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "arguments": self.arguments,
            "description": self.description,
        }


@dataclass
class ToolConfirmationResponse:
    """Value returned by the ``on_tool_confirm`` callback.

    * ``approved=True``  — proceed with execution.
    * ``approved=True, modified_arguments={...}``  — execute with new args.
    * ``approved=False`` — deny execution; an optional *reason* is recorded.

    For convenience the callback may also return a plain ``bool``
    (``True`` ≡ approved, ``False`` ≡ denied) or a ``dict`` with matching keys.
    """

    approved: bool = True
    modified_arguments: dict[str, Any] | None = None
    reason: str = ""

    @classmethod
    def from_raw(cls, raw: "bool | dict[str, Any] | ToolConfirmationResponse") -> "ToolConfirmationResponse":
        """Normalize the many accepted return types into a response object."""
        if isinstance(raw, ToolConfirmationResponse):
            return raw
        if isinstance(raw, bool):
            return cls(approved=raw)
        if isinstance(raw, dict):
            return cls(
                approved=raw.get("approved", True),
                modified_arguments=raw.get("modified_arguments"),
                reason=raw.get("reason", ""),
            )
        # Fallback: truthy check.
        return cls(approved=bool(raw))


__all__ = [
    "tool_parameter",
    "tool",
    "toolkit",
    "tool_decorator",
    "ToolConfirmationRequest",
    "ToolConfirmationResponse",
]
