import inspect
import json
from dataclasses import dataclass
from typing import Any, Callable

def _annotation_to_json_type(annotation: Any) -> str:
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

@dataclass
class LLM_tool_parameter:
    name: str
    description: str
    type_: str
    required: bool = False
    pattern: str | None = None

    def to_json(self) -> dict[str, Any]:
        json_parameter: dict[str, Any] = {
            "type": self.type_,
            "description": self.description,
        }
        if self.pattern is not None:
            json_parameter["pattern"] = self.pattern
        return json_parameter

class LLM_tool:
    def __init__(
        self,
        name: str = "",
        description: str = "",
        func: Callable[..., Any] | None = None,
        parameters: list[LLM_tool_parameter | dict[str, Any]] | None = None,
        observe: bool = False,
    ):
        self.name = name
        self.description = description
        self.func = func
        self.observe = observe
        self.parameters = self._construct_parameters(parameters)

        if self.func is not None and not self.parameters:
            self.parameters = self._infer_parameters_from_func(self.func)

        if not self.name and self.func is not None:
            self.name = self.func.__name__

        if not self.description and self.func is not None and self.func.__doc__:
            self.description = self.func.__doc__.strip().splitlines()[0]

    @classmethod
    def from_callable(
        cls,
        func: Callable[..., Any],
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: list[LLM_tool_parameter | dict[str, Any]] | None = None,
        observe: bool = False,
    ) -> "LLM_tool":
        return cls(
            name=name or func.__name__,
            description=description or (func.__doc__.strip().splitlines()[0] if func.__doc__ else ""),
            func=func,
            parameters=parameters,
            observe=observe,
        )

    def _construct_parameters(
        self,
        parameters: list[LLM_tool_parameter | dict[str, Any]] | None,
    ) -> list[LLM_tool_parameter]:
        constructed_parameters: list[LLM_tool_parameter] = []
        for parameter in parameters or []:
            if isinstance(parameter, LLM_tool_parameter):
                constructed_parameters.append(parameter)
            elif isinstance(parameter, dict):
                constructed_parameters.append(
                    LLM_tool_parameter(
                        name=parameter.get("name", ""),
                        description=parameter.get("description", ""),
                        type_=parameter.get("type_", "string"),
                        required=parameter.get("required", False),
                        pattern=parameter.get("pattern"),
                    )
                )
        return constructed_parameters

    def _infer_parameters_from_func(self, func: Callable[..., Any]) -> list[LLM_tool_parameter]:
        inferred: list[LLM_tool_parameter] = []
        signature = inspect.signature(func)
        for name, param in signature.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue

            annotation = param.annotation if param.annotation != inspect._empty else str
            type_ = _annotation_to_json_type(annotation)
            required = param.default == inspect._empty
            inferred.append(
                LLM_tool_parameter(
                    name=name,
                    description=f"Argument {name}",
                    type_=type_,
                    required=required,
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
            return {"error": "tool function not implemented (LLM_tool --> execute)"}

        try:
            if arguments is None:
                parsed_arguments: dict[str, Any] = {}
            elif isinstance(arguments, str):
                stripped = arguments.strip()
                parsed_arguments = json.loads(stripped) if stripped else {}
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

def llm_tool(
    *,
    name: str | None = None,
    description: str | None = None,
    parameters: list[LLM_tool_parameter | dict[str, Any]] | None = None,
    observe: bool = False,
):
    def decorator(func: Callable[..., Any]) -> LLM_tool:
        return LLM_tool.from_callable(
            func,
            name=name,
            description=description,
            parameters=parameters,
            observe=observe,
        )

    return decorator

class LLM_toolkit:
    def __init__(self, tools: dict[str, LLM_tool] | None = None):
        self.tools: dict[str, LLM_tool] = {}
        for tool_name, tool in (tools or {}).items():
            if isinstance(tool, LLM_tool):
                self.tools[tool_name] = tool

    def register(self, tool: LLM_tool | Callable[..., Any], *, observe: bool | None = None) -> LLM_tool:
        if isinstance(tool, LLM_tool):
            if observe is not None:
                tool.observe = observe
            self.tools[tool.name] = tool
            return tool

        if callable(tool):
            wrapped = LLM_tool.from_callable(tool, observe=bool(observe))
            self.tools[wrapped.name] = wrapped
            return wrapped

        raise ValueError("invalid tool passed to register")

    def get(self, function_name: str) -> LLM_tool | None:
        return self.tools.get(function_name)

    def execute(self, function_name: str, arguments: dict[str, Any] | str | None) -> dict[str, Any]:
        tool = self.get(function_name)
        if tool is None:
            return {"error": f"tool not found: {function_name}", "tool": function_name}
        return tool.execute(arguments)

    def to_json(self) -> list[dict[str, Any]]:
        return [tool.to_json() for tool in self.tools.values()]

__all__ = [
    "LLM_tool_parameter",
    "LLM_tool",
    "LLM_toolkit",
    "llm_tool",
]
