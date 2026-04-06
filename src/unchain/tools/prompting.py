from __future__ import annotations

from dataclasses import dataclass

from ..kernel.delta import HarnessDelta, InsertMessagesOp, ReplaceSpanOp
from .base import BaseToolHarness, ToolContext
from .models import ToolPromptSpec
from .tool import Tool
from .toolkit import Toolkit

TOOLS_BLOCK_HEADER = "# unchain generated tools guidance"
TOOLS_BLOCK_START = "<tools>"
TOOLS_BLOCK_END = "</tools>"


def _is_tools_block_message(message: dict[str, object]) -> bool:
    if not isinstance(message, dict) or message.get("role") != "system":
        return False
    content = message.get("content")
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    return stripped.startswith(TOOLS_BLOCK_START) and TOOLS_BLOCK_HEADER in stripped and stripped.endswith(TOOLS_BLOCK_END)


def _render_tool_lines(label: str, lines: tuple[str, ...]) -> list[str]:
    if not lines:
        return []
    rendered = [f"  {label}:"]
    rendered.extend(f"  - {line}" for line in lines)
    return rendered


def render_tool_prompt_entry(tool_obj: Tool) -> str:
    spec = tool_obj.prompt_spec if isinstance(tool_obj.prompt_spec, ToolPromptSpec) else None
    purpose = ""
    when_to_use: tuple[str, ...] = ()
    when_not_to_use: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    advanced_tips: tuple[str, ...] = ()
    if spec is not None:
        purpose = spec.purpose.strip()
        when_to_use = spec.when_to_use
        when_not_to_use = spec.when_not_to_use
        examples = spec.examples
        advanced_tips = spec.advanced_tips
    if not purpose:
        purpose = str(tool_obj.description or "").strip() or f"Use `{tool_obj.name}` when it is the best available tool."

    lines = [f"- {tool_obj.name}: {purpose}"]
    lines.extend(_render_tool_lines("Use when", when_to_use))
    lines.extend(_render_tool_lines("Avoid when", when_not_to_use))
    lines.extend(_render_tool_lines("Examples", examples))
    lines.extend(_render_tool_lines("Advanced tips", advanced_tips))
    return "\n".join(lines)


def render_tool_prompt_block(toolkit: Toolkit | None) -> str:
    tool_map = toolkit.tools if isinstance(toolkit, Toolkit) else {}
    if not tool_map:
        return ""

    body = "\n\n".join(render_tool_prompt_entry(tool_obj) for tool_obj in tool_map.values())
    return (
        f"{TOOLS_BLOCK_START}\n"
        f"{TOOLS_BLOCK_HEADER}\n"
        "Use provider-native tool schemas as the source of truth for callable arguments.\n"
        "This block is guidance about when to use tools and how to use them well.\n\n"
        f"{body}\n"
        f"{TOOLS_BLOCK_END}"
    )


@dataclass
class ToolPromptHarness(BaseToolHarness):
    name: str = "tool_prompt"
    phases: tuple[str, ...] = ("before_model",)
    order: int = 250

    def build_tool_delta(self, context: ToolContext) -> HarnessDelta | None:
        messages = context.latest_messages()
        rendered = render_tool_prompt_block(context.toolkit)
        existing_indexes = [idx for idx, message in enumerate(messages) if _is_tools_block_message(message)]

        if not rendered:
            if not existing_indexes:
                return None
            return HarnessDelta(
                created_by=self.created_by,
                ops=(ReplaceSpanOp(start=existing_indexes[0], end=existing_indexes[-1] + 1, messages=[]),),
            )

        new_message = {"role": "system", "content": rendered}
        if existing_indexes:
            current_message = messages[existing_indexes[0]]
            if current_message == new_message and len(existing_indexes) == 1:
                return None
            return HarnessDelta(
                created_by=self.created_by,
                ops=(
                    ReplaceSpanOp(
                        start=existing_indexes[0],
                        end=existing_indexes[-1] + 1,
                        messages=[new_message],
                    ),
                ),
            )

        insert_index = 0
        while insert_index < len(messages):
            message = messages[insert_index]
            if not isinstance(message, dict) or message.get("role") != "system":
                break
            insert_index += 1

        return HarnessDelta(
            created_by=self.created_by,
            ops=(InsertMessagesOp(index=insert_index, messages=[new_message]),),
        )


__all__ = [
    "TOOLS_BLOCK_END",
    "TOOLS_BLOCK_HEADER",
    "TOOLS_BLOCK_START",
    "ToolPromptHarness",
    "render_tool_prompt_block",
    "render_tool_prompt_entry",
]
