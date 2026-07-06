from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from ..kernel.types import ToolCall
from .models import ToolHistoryOptimizationContext
from .toolkit import Toolkit


@dataclass(frozen=True)
class ToolResultBudgetConfig:
    max_result_chars: int = 4_000
    max_batch_chars: int = 16_000
    preview_chars: int = 600
    min_chars_to_budget: int = 1_200


@dataclass(frozen=True)
class ToolResultBudgetStats:
    result_count: int = 0
    compacted_count: int = 0
    optimizer_error_count: int = 0
    original_chars: int = 0
    budgeted_chars: int = 0
    saved_chars: int = 0


@dataclass(frozen=True)
class ToolResultBudgetOutcome:
    messages: list[dict[str, Any]]
    stats: ToolResultBudgetStats


@dataclass
class _ResultRecord:
    message_index: int
    location_type: str
    tool_name: str
    call_id: str
    payload: Any
    original_chars: int
    block_index: int | None = None
    part_index: int | None = None


def _stable_json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        default=str,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _safe_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return copy.deepcopy(value)
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except Exception:
        return value


def _preview_text(text: str, preview_chars: int) -> str:
    limit = max(1, int(preview_chars))
    if len(text) <= limit * 2:
        return text
    head = text[:limit]
    tail = text[-limit:]
    omitted = len(text) - len(head) - len(tail)
    return f"{head}\n... <omitted {omitted} chars> ...\n{tail}"


def _sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="replace")).hexdigest()


def _budgeted_chars(payload: Any) -> int:
    return len(_stable_json_dumps(payload))


def _compact_payload(
    payload: Any,
    *,
    reason: str,
    tool_name: str,
    call_id: str,
    preview_chars: int,
) -> dict[str, Any]:
    original_json = _stable_json_dumps(payload)
    compacted: dict[str, Any] = {
        "compacted": True,
        "reason": reason,
        "tool_name": tool_name,
        "call_id": call_id,
        "original_chars": len(original_json),
        "budgeted_chars": 0,
        "preview": _preview_text(original_json, preview_chars),
        "original_sha1": _sha1_text(original_json),
    }
    previous_size = -1
    while True:
        current_size = _budgeted_chars(compacted)
        if current_size == previous_size:
            break
        compacted["budgeted_chars"] = current_size
        previous_size = current_size
    return compacted


def _tool_call_maps(
    tool_calls: list[ToolCall],
) -> tuple[dict[str, ToolCall], dict[str, list[ToolCall]]]:
    by_id: dict[str, ToolCall] = {}
    by_name: dict[str, list[ToolCall]] = {}
    for call in tool_calls:
        if call.call_id:
            by_id[call.call_id] = call
        by_name.setdefault(call.name, []).append(call)
    return by_id, by_name


def _resolve_call(
    *,
    call_id: str,
    tool_name: str,
    by_id: dict[str, ToolCall],
    by_name: dict[str, list[ToolCall]],
    used_call_ids: set[str],
) -> tuple[str, str]:
    if call_id and call_id in by_id:
        call = by_id[call_id]
        used_call_ids.add(call.call_id)
        return call.name, call.call_id

    if tool_name:
        for call in by_name.get(tool_name, []):
            if call.call_id not in used_call_ids:
                used_call_ids.add(call.call_id)
                return call.name, call.call_id
        calls = by_name.get(tool_name, [])
        if calls:
            return calls[0].name, calls[0].call_id

    return tool_name, call_id


def _collect_result_records(
    messages: list[dict[str, Any]],
    *,
    provider: str,
    tool_calls: list[ToolCall],
) -> list[_ResultRecord]:
    del provider
    by_id, by_name = _tool_call_maps(tool_calls)
    used_call_ids: set[str] = set()
    records: list[_ResultRecord] = []

    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        if message.get("type") == "function_call_output":
            call_id = str(message.get("call_id") or "")
            tool_name, resolved_call_id = _resolve_call(
                call_id=call_id,
                tool_name="",
                by_id=by_id,
                by_name=by_name,
                used_call_ids=used_call_ids,
            )
            payload = _safe_json_loads(message.get("output", ""))
            records.append(
                _ResultRecord(
                    message_index=message_index,
                    location_type="openai_function_call_output",
                    tool_name=tool_name,
                    call_id=resolved_call_id,
                    payload=payload,
                    original_chars=_budgeted_chars(payload),
                )
            )

        content = message.get("content")
        if isinstance(content, list):
            for block_index, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                call_id = str(block.get("tool_use_id") or "")
                tool_name, resolved_call_id = _resolve_call(
                    call_id=call_id,
                    tool_name="",
                    by_id=by_id,
                    by_name=by_name,
                    used_call_ids=used_call_ids,
                )
                payload = _safe_json_loads(block.get("content", ""))
                records.append(
                    _ResultRecord(
                        message_index=message_index,
                        location_type="content_block_tool_result",
                        tool_name=tool_name,
                        call_id=resolved_call_id,
                        payload=payload,
                        original_chars=_budgeted_chars(payload),
                        block_index=block_index,
                    )
                )

        if message.get("role") == "tool" and "content" in message:
            call_id = str(message.get("tool_call_id") or "")
            tool_name, resolved_call_id = _resolve_call(
                call_id=call_id,
                tool_name="",
                by_id=by_id,
                by_name=by_name,
                used_call_ids=used_call_ids,
            )
            payload = _safe_json_loads(message.get("content", ""))
            records.append(
                _ResultRecord(
                    message_index=message_index,
                    location_type="role_tool_content",
                    tool_name=tool_name,
                    call_id=resolved_call_id,
                    payload=payload,
                    original_chars=_budgeted_chars(payload),
                )
            )

        parts = message.get("parts")
        if isinstance(parts, list):
            for part_index, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                function_response = part.get("function_response")
                if not isinstance(function_response, dict):
                    continue
                tool_name = str(function_response.get("name") or "")
                call_id = str(function_response.get("id") or part.get("id") or "")
                resolved_tool_name, resolved_call_id = _resolve_call(
                    call_id=call_id,
                    tool_name=tool_name,
                    by_id=by_id,
                    by_name=by_name,
                    used_call_ids=used_call_ids,
                )
                payload = _safe_json_loads(function_response.get("response", {}))
                records.append(
                    _ResultRecord(
                        message_index=message_index,
                        location_type="parts_function_response",
                        tool_name=resolved_tool_name,
                        call_id=resolved_call_id,
                        payload=payload,
                        original_chars=_budgeted_chars(payload),
                        part_index=part_index,
                    )
                )

    return records


def _write_record_payload(
    messages: list[dict[str, Any]],
    record: _ResultRecord,
    payload: Any,
) -> None:
    if record.message_index < 0 or record.message_index >= len(messages):
        return
    message = messages[record.message_index]
    if not isinstance(message, dict):
        return

    if record.location_type == "openai_function_call_output":
        message["output"] = _stable_json_dumps(payload)
        return

    if record.location_type == "content_block_tool_result":
        content = message.get("content")
        if not isinstance(content, list) or record.block_index is None:
            return
        if record.block_index >= len(content) or not isinstance(content[record.block_index], dict):
            return
        content[record.block_index]["content"] = _stable_json_dumps(payload)
        return

    if record.location_type == "role_tool_content":
        message["content"] = _stable_json_dumps(payload)
        return

    if record.location_type == "parts_function_response":
        parts = message.get("parts")
        if not isinstance(parts, list) or record.part_index is None:
            return
        if record.part_index >= len(parts) or not isinstance(parts[record.part_index], dict):
            return
        function_response = parts[record.part_index].get("function_response")
        if not isinstance(function_response, dict):
            return
        function_response["response"] = (
            copy.deepcopy(payload)
            if isinstance(payload, dict)
            else {"value": copy.deepcopy(payload)}
        )


class ToolResultBudgetController:
    def __init__(self, config: ToolResultBudgetConfig | None = None) -> None:
        self.config = config or ToolResultBudgetConfig()

    def budget_messages(
        self,
        *,
        provider: str,
        toolkit: Toolkit | None,
        tool_calls: list[ToolCall],
        result_messages: list[dict[str, Any]],
        session_id: str = "",
        latest_messages: list[dict[str, Any]] | None = None,
        reason: str = "tool_result_budget",
    ) -> ToolResultBudgetOutcome:
        normalized_provider = str(provider or "").strip().lower()
        messages = copy.deepcopy(result_messages)
        records = _collect_result_records(
            messages,
            provider=normalized_provider,
            tool_calls=tool_calls,
        )
        if not records:
            return ToolResultBudgetOutcome(
                messages=messages,
                stats=ToolResultBudgetStats(),
            )

        original_total = sum(record.original_chars for record in records)
        max_result_chars = max(1, int(self.config.max_result_chars))
        max_batch_chars = max(1, int(self.config.max_batch_chars))
        min_chars_to_budget = max(0, int(self.config.min_chars_to_budget))
        selected_indexes: set[int] = {
            index
            for index, record in enumerate(records)
            if record.original_chars >= min_chars_to_budget
            and record.original_chars > max_result_chars
        }

        current_chars = {
            index: record.original_chars
            for index, record in enumerate(records)
        }
        for index in selected_indexes:
            current_chars[index] = _estimate_generic_compacted_chars(
                records[index],
                reason=reason,
                preview_chars=self.config.preview_chars,
            )
        for index in sorted(
            range(len(records)),
            key=lambda candidate: records[candidate].original_chars,
            reverse=True,
        ):
            if sum(current_chars.values()) <= max_batch_chars:
                break
            if index in selected_indexes:
                continue
            record = records[index]
            if record.original_chars < min_chars_to_budget:
                continue
            selected_indexes.add(index)
            current_chars[index] = _estimate_generic_compacted_chars(
                record,
                reason=reason,
                preview_chars=self.config.preview_chars,
            )

        compacted_payloads: dict[int, Any] = {}
        optimizer_error_count = 0
        latest_context_messages = copy.deepcopy(latest_messages) if latest_messages is not None else []
        for index in sorted(selected_indexes):
            record = records[index]
            optimized: Any | None = None
            optimizer = None
            if toolkit is not None and record.tool_name:
                tool_obj = toolkit.get(record.tool_name)
                if tool_obj is not None:
                    optimizer = getattr(tool_obj, "history_result_optimizer", None)

            context = ToolHistoryOptimizationContext(
                tool_name=record.tool_name,
                call_id=record.call_id,
                kind="result",
                provider=normalized_provider,
                session_id=session_id,
                latest_messages=copy.deepcopy(latest_context_messages),
                max_chars=max_result_chars,
                preview_chars=max(1, int(self.config.preview_chars)),
                include_hash=True,
            )
            if callable(optimizer):
                try:
                    optimized = optimizer(copy.deepcopy(record.payload), context)
                except Exception:
                    optimizer_error_count += 1
                    optimized = None

            if optimized is None:
                optimized = _compact_payload(
                    record.payload,
                    reason=reason,
                    tool_name=record.tool_name,
                    call_id=record.call_id,
                    preview_chars=self.config.preview_chars,
                )

            compacted_payloads[index] = optimized
            _write_record_payload(messages, record, optimized)

        final_chars = 0
        compacted_count = 0
        for index, record in enumerate(records):
            payload = compacted_payloads.get(index, record.payload)
            payload_chars = _budgeted_chars(payload)
            final_chars += payload_chars
            if _stable_json_dumps(payload) != _stable_json_dumps(record.payload):
                compacted_count += 1

        return ToolResultBudgetOutcome(
            messages=messages,
            stats=ToolResultBudgetStats(
                result_count=len(records),
                compacted_count=compacted_count,
                optimizer_error_count=optimizer_error_count,
                original_chars=original_total,
                budgeted_chars=final_chars,
                saved_chars=max(0, original_total - final_chars),
            ),
        )


def _estimate_generic_compacted_chars(
    record: _ResultRecord,
    *,
    reason: str,
    preview_chars: int,
) -> int:
    return _budgeted_chars(
        _compact_payload(
            record.payload,
            reason=reason,
            tool_name=record.tool_name,
            call_id=record.call_id,
            preview_chars=preview_chars,
        )
    )


__all__ = [
    "ToolResultBudgetConfig",
    "ToolResultBudgetController",
    "ToolResultBudgetOutcome",
    "ToolResultBudgetStats",
]
