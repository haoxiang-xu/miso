from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Literal

from .tool import _escape_control_chars_inside_json_strings, tool, tool_parameter

REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"
HUMAN_INPUT_KIND_SELECTOR = "selector"
HUMAN_INPUT_OTHER_VALUE = "__other__"


def _parse_tool_arguments(arguments: dict[str, Any] | str | None) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return copy.deepcopy(arguments)
    if isinstance(arguments, str):
        raw = arguments.strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            repaired = _escape_control_chars_inside_json_strings(raw)
            return json.loads(repaired)
    raise ValueError("human input tool arguments must be a dict or JSON string")


def _clean_required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _clean_optional_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("optional text fields must be strings when provided")
    return value


def _clean_optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer when provided")
    return value


@dataclass
class HumanInputOption:
    label: str
    value: str
    description: str = ""

    @classmethod
    def from_raw(cls, raw: Any) -> "HumanInputOption":
        if not isinstance(raw, dict):
            raise ValueError("each selector option must be an object")

        label = _clean_required_text(raw.get("label"), "option.label")
        value = _clean_required_text(raw.get("value"), "option.value")
        description = _clean_optional_text(raw.get("description", ""))

        if value == HUMAN_INPUT_OTHER_VALUE:
            raise ValueError(f"option.value cannot use reserved value '{HUMAN_INPUT_OTHER_VALUE}'")

        return cls(label=label, value=value, description=description)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "value": self.value,
            "description": self.description,
        }


@dataclass
class HumanInputRequest:
    request_id: str
    kind: Literal["selector"]
    title: str
    question: str
    selection_mode: Literal["single", "multiple"]
    options: list[HumanInputOption]
    allow_other: bool = False
    other_label: str = "Other"
    other_placeholder: str = ""
    min_selected: int | None = None
    max_selected: int | None = None

    @classmethod
    def from_tool_arguments(
        cls,
        arguments: dict[str, Any] | str | None,
        *,
        request_id: str,
    ) -> "HumanInputRequest":
        raw = _parse_tool_arguments(arguments)
        title = _clean_required_text(raw.get("title"), "title")
        question = _clean_required_text(raw.get("question"), "question")
        selection_mode = raw.get("selection_mode")
        if selection_mode not in {"single", "multiple"}:
            raise ValueError("selection_mode must be 'single' or 'multiple'")

        raw_options = raw.get("options")
        if not isinstance(raw_options, list) or not raw_options:
            raise ValueError("options must be a non-empty array")

        options = [HumanInputOption.from_raw(item) for item in raw_options]
        seen_values: set[str] = set()
        for option in options:
            if option.value in seen_values:
                raise ValueError(f"duplicate option value: {option.value}")
            seen_values.add(option.value)

        raw_allow_other = raw.get("allow_other", False)
        if not isinstance(raw_allow_other, bool):
            raise ValueError("allow_other must be a boolean")
        allow_other = raw_allow_other
        other_label = _clean_optional_text(raw.get("other_label", "Other")) or "Other"
        other_placeholder = _clean_optional_text(raw.get("other_placeholder", ""))
        min_selected = _clean_optional_int(raw.get("min_selected"), "min_selected")
        max_selected = _clean_optional_int(raw.get("max_selected"), "max_selected")

        if selection_mode == "single":
            if min_selected is None:
                min_selected = 1
            if max_selected is None:
                max_selected = 1
            if min_selected not in {0, 1}:
                raise ValueError("single selection mode only supports min_selected of 0 or 1")
            if max_selected != 1:
                raise ValueError("single selection mode requires max_selected to be 1")
        else:
            allowed_total = len(options) + (1 if allow_other else 0)
            if min_selected is None:
                min_selected = 1
            if max_selected is None:
                max_selected = allowed_total

        if min_selected is None or max_selected is None:
            raise ValueError("min_selected and max_selected could not be resolved")
        if min_selected < 0:
            raise ValueError("min_selected must be >= 0")
        if max_selected < 1:
            raise ValueError("max_selected must be >= 1")
        if min_selected > max_selected:
            raise ValueError("min_selected cannot be greater than max_selected")

        allowed_total = len(options) + (1 if allow_other else 0)
        if max_selected > allowed_total:
            raise ValueError("max_selected cannot exceed the total number of available selections")

        return cls(
            request_id=request_id,
            kind=HUMAN_INPUT_KIND_SELECTOR,
            title=title,
            question=question,
            selection_mode=selection_mode,
            options=options,
            allow_other=allow_other,
            other_label=other_label,
            other_placeholder=other_placeholder,
            min_selected=min_selected,
            max_selected=max_selected,
        )

    @classmethod
    def from_dict(cls, raw: Any) -> "HumanInputRequest":
        if isinstance(raw, HumanInputRequest):
            return raw
        if not isinstance(raw, dict):
            raise ValueError("human input request must be an object")
        request_id = _clean_required_text(raw.get("request_id"), "request_id")
        kind = raw.get("kind")
        if kind != HUMAN_INPUT_KIND_SELECTOR:
            raise ValueError("human input request kind must be 'selector'")
        payload = {
            "title": raw.get("title"),
            "question": raw.get("question"),
            "selection_mode": raw.get("selection_mode"),
            "options": raw.get("options"),
            "allow_other": raw.get("allow_other", False),
            "other_label": raw.get("other_label", "Other"),
            "other_placeholder": raw.get("other_placeholder", ""),
            "min_selected": raw.get("min_selected"),
            "max_selected": raw.get("max_selected"),
        }
        return cls.from_tool_arguments(payload, request_id=request_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "kind": self.kind,
            "title": self.title,
            "question": self.question,
            "selection_mode": self.selection_mode,
            "options": [option.to_dict() for option in self.options],
            "allow_other": self.allow_other,
            "other_label": self.other_label,
            "other_placeholder": self.other_placeholder,
            "min_selected": self.min_selected,
            "max_selected": self.max_selected,
        }

    def allowed_values(self) -> set[str]:
        values = {option.value for option in self.options}
        if self.allow_other:
            values.add(HUMAN_INPUT_OTHER_VALUE)
        return values


@dataclass
class HumanInputResponse:
    request_id: str
    selected_values: list[str]
    other_text: str | None = None

    @classmethod
    def from_raw(
        cls,
        raw: Any,
        *,
        request: HumanInputRequest,
    ) -> "HumanInputResponse":
        if isinstance(raw, HumanInputResponse):
            raw = raw.to_dict()
        if not isinstance(raw, dict):
            raise ValueError("human input response must be an object")

        request_id = _clean_required_text(raw.get("request_id"), "request_id")
        if request_id != request.request_id:
            raise ValueError("human input response request_id does not match the pending request")

        selected_values = raw.get("selected_values")
        if not isinstance(selected_values, list):
            raise ValueError("selected_values must be an array")
        normalized_values: list[str] = []
        seen_values: set[str] = set()
        for value in selected_values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("selected_values must contain non-empty strings")
            normalized = value.strip()
            if normalized in seen_values:
                raise ValueError(f"duplicate selected value: {normalized}")
            seen_values.add(normalized)
            normalized_values.append(normalized)

        count = len(normalized_values)
        min_selected = request.min_selected or 0
        max_selected = request.max_selected or 0
        if count < min_selected:
            raise ValueError(f"selected_values must contain at least {min_selected} item(s)")
        if max_selected and count > max_selected:
            raise ValueError(f"selected_values cannot contain more than {max_selected} item(s)")

        allowed_values = request.allowed_values()
        invalid_values = [value for value in normalized_values if value not in allowed_values]
        if invalid_values:
            raise ValueError(f"selected_values contains unsupported option(s): {invalid_values}")

        raw_other_text = raw.get("other_text")
        other_text: str | None = None
        if raw_other_text is not None:
            if not isinstance(raw_other_text, str):
                raise ValueError("other_text must be a string when provided")
            other_text = raw_other_text.strip() or None

        selected_other = HUMAN_INPUT_OTHER_VALUE in normalized_values
        if selected_other:
            if not request.allow_other:
                raise ValueError("other selection is not allowed for this request")
            if not other_text:
                raise ValueError("other_text is required when '__other__' is selected")
        elif other_text:
            raise ValueError("other_text can only be provided when '__other__' is selected")

        return cls(
            request_id=request_id,
            selected_values=normalized_values,
            other_text=other_text,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "selected_values": list(self.selected_values),
            "other_text": self.other_text,
        }

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "submitted": True,
            "selected_values": list(self.selected_values),
            "other_text": self.other_text,
        }


def build_request_user_input_tool() -> tool:
    option_item_schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "User-facing option label."},
            "value": {"type": "string", "description": "Stable option value returned to the assistant."},
            "description": {"type": "string", "description": "Optional helper text for the user."},
        },
        "required": ["label", "value"],
        "additionalProperties": False,
    }

    return tool(
        name=REQUEST_USER_INPUT_TOOL_NAME,
        description=(
            "Ask the user to choose from a structured selector UI and suspend the run until they respond. "
            "Strongly prefer this whenever there are multiple plausible approaches, product directions, "
            "technical stacks, UX choices, or requirement interpretations that would materially change the outcome. "
            "When several reasonable paths exist, ask the user instead of silently guessing."
        ),
        func=lambda **_: {"error": "request_user_input is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            tool_parameter(
                name="title",
                description="Short title shown above the selector.",
                type_="string",
                required=True,
            ),
            tool_parameter(
                name="question",
                description="Prompt shown to the user explaining what they should choose.",
                type_="string",
                required=True,
            ),
            tool_parameter(
                name="selection_mode",
                description="Selection mode. Must be 'single' or 'multiple'.",
                type_="string",
                required=True,
            ),
            tool_parameter(
                name="options",
                description="Available options shown to the user.",
                type_="array",
                required=True,
                items=option_item_schema,
            ),
            tool_parameter(
                name="allow_other",
                description="Whether to show an Other option that allows freeform input.",
                type_="boolean",
                required=False,
            ),
            tool_parameter(
                name="other_label",
                description="Label for the Other option when allow_other is true.",
                type_="string",
                required=False,
            ),
            tool_parameter(
                name="other_placeholder",
                description="Placeholder for the freeform input shown when Other is selected.",
                type_="string",
                required=False,
            ),
            tool_parameter(
                name="min_selected",
                description="Minimum number of selections required.",
                type_="integer",
                required=False,
            ),
            tool_parameter(
                name="max_selected",
                description="Maximum number of selections allowed.",
                type_="integer",
                required=False,
            ),
        ],
    )


__all__ = [
    "REQUEST_USER_INPUT_TOOL_NAME",
    "HUMAN_INPUT_KIND_SELECTOR",
    "HUMAN_INPUT_OTHER_VALUE",
    "HumanInputOption",
    "HumanInputRequest",
    "HumanInputResponse",
    "build_request_user_input_tool",
]
