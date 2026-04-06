from __future__ import annotations

import copy
from typing import Any

from .base import BaseContextOptimizer, OptimizerContext
from .common import split_system_and_non_system


def _extract_tool_use_ids(message: dict[str, Any]) -> set[str]:
    """Extract all tool_use block IDs from an assistant message."""
    ids: set[str] = set()
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                block_id = block.get("id")
                if isinstance(block_id, str) and block_id:
                    ids.add(block_id)
    # OpenAI format
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_id = tc.get("id")
                if isinstance(tc_id, str) and tc_id:
                    ids.add(tc_id)
    return ids


def _extract_tool_result_ids(message: dict[str, Any]) -> set[str]:
    """Extract all tool_result IDs from a user/tool message."""
    ids: set[str] = set()
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                use_id = block.get("tool_use_id")
                if isinstance(use_id, str) and use_id:
                    ids.add(use_id)
    # OpenAI format
    tc_id = message.get("tool_call_id")
    if isinstance(tc_id, str) and tc_id:
        ids.add(tc_id)
    return ids


def _remove_orphaned_tool_uses(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove tool_use blocks that don't have matching tool_result in the next message(s).

    Scans the non-system messages and collects all tool_result IDs. Any assistant
    message containing tool_use blocks whose IDs are not in the result set gets
    those blocks stripped. If the assistant message becomes empty, it's removed.
    """
    all_result_ids: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        all_result_ids.update(_extract_tool_result_ids(msg))

    cleaned: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            cleaned.append(msg)
            continue
        role = msg.get("role")
        if role != "assistant":
            cleaned.append(copy.deepcopy(msg))
            continue

        use_ids = _extract_tool_use_ids(msg)
        orphaned_ids = use_ids - all_result_ids
        if not orphaned_ids:
            cleaned.append(copy.deepcopy(msg))
            continue

        # Strip orphaned tool_use blocks
        new_msg = copy.deepcopy(msg)
        content = new_msg.get("content")
        if isinstance(content, list):
            new_msg["content"] = [
                block for block in content
                if not (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and block.get("id") in orphaned_ids
                )
            ]
            if not new_msg["content"]:
                new_msg["content"] = ""

        tool_calls = new_msg.get("tool_calls")
        if isinstance(tool_calls, list):
            new_msg["tool_calls"] = [
                tc for tc in tool_calls
                if not (isinstance(tc, dict) and tc.get("id") in orphaned_ids)
            ]
            if not new_msg["tool_calls"]:
                del new_msg["tool_calls"]

        has_content = new_msg.get("content") not in ("", [], None)
        has_tool_calls = bool(new_msg.get("tool_calls"))
        if has_content or has_tool_calls:
            cleaned.append(new_msg)

    return cleaned


class ToolPairSafetyOptimizer(BaseContextOptimizer):
    """Safety net: ensure every tool_use has a matching tool_result.

    Runs at order 60 (after all other optimizers including ContextUsage)
    to clean up any orphaned tool_use blocks that other optimizers may
    have created by truncating messages.
    """

    def __init__(self, *, phases=("before_model",), order: int = 60) -> None:
        super().__init__(name="tool_pair_safety", phases=phases, order=order)

    def build_optimizer_delta(self, context: OptimizerContext):
        messages = context.latest_messages()
        systems, non_system = split_system_and_non_system(messages)
        cleaned = _remove_orphaned_tool_uses(non_system)
        if len(cleaned) == len(non_system):
            return None
        from .common import replace_non_system_span
        updated = replace_non_system_span(messages, cleaned)
        bucket = {
            "removed_orphans": len(non_system) - len(cleaned),
        }
        return self.replace_messages_delta(context, updated, bucket=bucket)
