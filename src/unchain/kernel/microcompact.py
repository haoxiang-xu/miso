from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Any

from .delta import HarnessDelta, ReplaceSpanOp
from .harness import BaseRuntimeHarness, HarnessContext, RuntimePhase
from .types import ToolCall
from ..tools.result_budget import ToolResultBudgetConfig, ToolResultBudgetController
from ..tools.toolkit import Toolkit


_TOKEN_CHARS = 4


@dataclass(frozen=True)
class MidRunMicrocompactConfig:
    enabled: bool = True
    trigger_context_ratio: float = 0.85
    trigger_remaining_tokens: int = 12_000
    keep_recent_completed_turns: int = 1
    compact_current_batch: bool = False
    min_savings_chars: int = 8_000
    max_compacted_result_chars: int = 1_200
    preview_chars: int = 300

    @classmethod
    def from_raw(cls, raw: Any) -> "MidRunMicrocompactConfig":
        if isinstance(raw, cls):
            return raw
        defaults = cls()
        if not isinstance(raw, dict):
            return defaults

        def bool_field(name: str) -> bool:
            value = raw.get(name, getattr(defaults, name))
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"1", "true", "yes", "on"}:
                    return True
                if lowered in {"0", "false", "no", "off"}:
                    return False
            return bool(getattr(defaults, name))

        def int_field(name: str, minimum: int) -> int:
            value = raw.get(name, getattr(defaults, name))
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = getattr(defaults, name)
            return max(minimum, parsed)

        def float_field(name: str, minimum: float) -> float:
            value = raw.get(name, getattr(defaults, name))
            try:
                parsed = float(value)
            except (TypeError, ValueError):
                parsed = getattr(defaults, name)
            return max(minimum, parsed)

        return cls(
            enabled=bool_field("enabled"),
            trigger_context_ratio=float_field("trigger_context_ratio", 0.0),
            trigger_remaining_tokens=int_field("trigger_remaining_tokens", 0),
            keep_recent_completed_turns=int_field("keep_recent_completed_turns", 0),
            compact_current_batch=bool_field("compact_current_batch"),
            min_savings_chars=int_field("min_savings_chars", 0),
            max_compacted_result_chars=int_field("max_compacted_result_chars", 1),
            preview_chars=int_field("preview_chars", 0),
        )


@dataclass(frozen=True)
class _ResultRecord:
    ordinal: int
    message_index: int
    location_type: str
    call_id: str
    call_occurrence: int
    tool_name: str
    payload: Any
    original_chars: int
    block_index: int | None = None
    part_index: int | None = None

    @property
    def identity(self) -> tuple[Any, ...]:
        if self.call_id:
            return ("call", self.call_id, self.call_occurrence)
        return ("location", self.message_index, self.location_type, self.block_index, self.part_index)


@dataclass(frozen=True)
class _Pressure:
    source_chars: int
    estimated_tokens: int
    max_context_window_tokens: int
    context_ratio: float
    remaining_tokens: int
    triggered_by_context_ratio: bool
    triggered_by_remaining_tokens: bool

    @property
    def triggered(self) -> bool:
        return self.triggered_by_context_ratio or self.triggered_by_remaining_tokens


@dataclass(frozen=True)
class _CompactMessagesOutcome:
    messages: list[dict[str, Any]]
    result_count: int
    compacted_count: int
    optimizer_error_count: int
    original_chars: int
    budgeted_chars: int
    saved_chars: int


def _stable_json_dumps(value: Any) -> str:
    try:
        return json.dumps(
            value,
            default=str,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except Exception:
        return repr(value)


def _message_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(_stable_json_dumps(message)) for message in messages if isinstance(message, dict))


def _payload_chars(payload: Any) -> int:
    if isinstance(payload, str):
        return len(payload)
    return len(_stable_json_dumps(payload))


def _parsed_payload(payload: Any) -> Any:
    if not isinstance(payload, str):
        return payload
    stripped = payload.strip()
    if not stripped or stripped[0] not in "[{":
        return payload
    try:
        return json.loads(stripped)
    except Exception:
        return payload


def _is_compacted_payload(payload: Any) -> bool:
    parsed = _parsed_payload(payload)
    return isinstance(parsed, dict) and parsed.get("compacted") is True


def _tool_call_from_any(value: Any) -> ToolCall | None:
    if isinstance(value, ToolCall):
        return value
    if not isinstance(value, dict):
        return None

    call_id = value.get("call_id") or value.get("id") or value.get("tool_call_id")
    name = value.get("name")
    function = value.get("function")
    if not name and isinstance(function, dict):
        name = function.get("name")
    if not call_id and not name:
        return None
    return ToolCall(call_id=str(call_id or ""), name=str(name or ""), arguments=value.get("arguments"))


def _event_tool_calls(event: dict[str, Any]) -> list[ToolCall]:
    raw_calls = event.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    calls: list[ToolCall] = []
    for raw_call in raw_calls:
        call = _tool_call_from_any(raw_call)
        if call is not None:
            calls.append(call)
    return calls


def _tool_call_names(messages: list[dict[str, Any]], extra_tool_calls: list[ToolCall]) -> dict[str, str]:
    names: dict[str, str] = {
        call.call_id: call.name
        for call in extra_tool_calls
        if call.call_id and call.name
    }

    for message in messages:
        if not isinstance(message, dict):
            continue

        if message.get("type") == "function_call":
            call_id = str(message.get("call_id") or message.get("id") or "")
            name = str(message.get("name") or "")
            if call_id and name:
                names.setdefault(call_id, name)

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for raw_call in tool_calls:
                call = _tool_call_from_any(raw_call)
                if call is not None and call.call_id and call.name:
                    names.setdefault(call.call_id, call.name)

        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                call_id = str(block.get("id") or "")
                name = str(block.get("name") or "")
                if call_id and name:
                    names.setdefault(call_id, name)

        parts = message.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                function_call = part.get("function_call")
                if not isinstance(function_call, dict):
                    continue
                call_id = str(function_call.get("id") or part.get("id") or "")
                name = str(function_call.get("name") or "")
                if call_id and name:
                    names.setdefault(call_id, name)

    return names


def _collect_result_records(
    messages: list[dict[str, Any]],
    *,
    tool_calls: list[ToolCall],
) -> list[_ResultRecord]:
    tool_names = _tool_call_names(messages, tool_calls)
    occurrence_by_call_id: dict[str, int] = {}
    records: list[_ResultRecord] = []

    def append_record(
        *,
        message_index: int,
        location_type: str,
        call_id: str,
        tool_name: str = "",
        payload: Any,
        block_index: int | None = None,
        part_index: int | None = None,
    ) -> None:
        normalized_call_id = str(call_id or "")
        occurrence = occurrence_by_call_id.get(normalized_call_id, 0) + 1
        occurrence_by_call_id[normalized_call_id] = occurrence
        resolved_tool_name = str(tool_name or tool_names.get(normalized_call_id, "") or "")
        records.append(
            _ResultRecord(
                ordinal=len(records),
                message_index=message_index,
                location_type=location_type,
                call_id=normalized_call_id,
                call_occurrence=occurrence,
                tool_name=resolved_tool_name,
                payload=copy.deepcopy(payload),
                original_chars=_payload_chars(payload),
                block_index=block_index,
                part_index=part_index,
            )
        )

    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        if message.get("type") == "function_call_output":
            append_record(
                message_index=message_index,
                location_type="openai_function_call_output",
                call_id=str(message.get("call_id") or ""),
                payload=message.get("output", ""),
            )

        content = message.get("content")
        if isinstance(content, list):
            for block_index, block in enumerate(content):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                append_record(
                    message_index=message_index,
                    location_type="content_block_tool_result",
                    call_id=str(block.get("tool_use_id") or ""),
                    payload=block.get("content", ""),
                    block_index=block_index,
                )

        if message.get("role") == "tool" and "content" in message:
            append_record(
                message_index=message_index,
                location_type="role_tool_content",
                call_id=str(message.get("tool_call_id") or ""),
                payload=message.get("content", ""),
            )

        parts = message.get("parts")
        if isinstance(parts, list):
            for part_index, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                function_response = part.get("function_response")
                if not isinstance(function_response, dict):
                    continue
                append_record(
                    message_index=message_index,
                    location_type="parts_function_response",
                    call_id=str(function_response.get("id") or part.get("id") or ""),
                    tool_name=str(function_response.get("name") or ""),
                    payload=function_response.get("response", {}),
                    part_index=part_index,
                )

    return records


def _is_tool_call_message(message: dict[str, Any]) -> bool:
    if message.get("type") == "function_call":
        return True

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list) and any(isinstance(item, dict) for item in tool_calls):
        return True

    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return True

    parts = message.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("function_call"), dict):
                return True

    return False


def _is_tool_result_message(message: dict[str, Any]) -> bool:
    if message.get("type") == "function_call_output":
        return True

    if message.get("role") == "tool" and "content" in message:
        return True

    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return True

    parts = message.get("parts")
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("function_response"), dict):
                return True

    return False


def _completed_tool_turn_identities(
    messages: list[dict[str, Any]],
    records: list[_ResultRecord],
) -> list[set[tuple[Any, ...]]]:
    records_by_message_index: dict[int, list[_ResultRecord]] = {}
    for record in records:
        records_by_message_index.setdefault(record.message_index, []).append(record)

    turns: list[set[tuple[Any, ...]]] = []
    current_turn: set[tuple[Any, ...]] | None = None

    def close_turn() -> None:
        nonlocal current_turn
        if current_turn:
            turns.append(current_turn)
        current_turn = None

    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        is_result = _is_tool_result_message(message)
        if _is_tool_call_message(message):
            close_turn()
            current_turn = set()
        elif message.get("role") == "assistant" and not is_result:
            close_turn()
        elif message.get("role") == "user" and not is_result:
            close_turn()

        if not is_result:
            continue

        if current_turn is None:
            current_turn = set()
        for record in records_by_message_index.get(message_index, []):
            current_turn.add(record.identity)

    close_turn()
    return turns


def _synthetic_message(record: _ResultRecord) -> dict[str, Any]:
    if record.location_type == "openai_function_call_output":
        return {
            "type": "function_call_output",
            "call_id": record.call_id,
            "output": copy.deepcopy(record.payload),
        }
    if record.location_type == "content_block_tool_result":
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": record.call_id,
                    "content": copy.deepcopy(record.payload),
                }
            ],
        }
    if record.location_type == "role_tool_content":
        return {
            "role": "tool",
            "tool_call_id": record.call_id,
            "content": copy.deepcopy(record.payload),
        }
    if record.location_type == "parts_function_response":
        function_response = {
            "name": record.tool_name,
            "response": copy.deepcopy(record.payload),
        }
        if record.call_id:
            function_response["id"] = record.call_id
        return {
            "role": "user",
            "parts": [{"function_response": function_response}],
        }
    return {}


def _synthetic_payload(record: _ResultRecord, message: dict[str, Any]) -> Any:
    if record.location_type == "openai_function_call_output":
        return message.get("output", "")
    if record.location_type == "content_block_tool_result":
        content = message.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            return content[0].get("content", "")
        return ""
    if record.location_type == "role_tool_content":
        return message.get("content", "")
    if record.location_type == "parts_function_response":
        parts = message.get("parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], dict):
            response = parts[0].get("function_response")
            if isinstance(response, dict):
                return response.get("response", {})
        return {}
    return None


def _write_record_payload(messages: list[dict[str, Any]], record: _ResultRecord, payload: Any) -> None:
    if record.message_index < 0 or record.message_index >= len(messages):
        return
    message = messages[record.message_index]
    if not isinstance(message, dict):
        return

    if record.location_type == "openai_function_call_output":
        message["output"] = copy.deepcopy(payload)
        return

    if record.location_type == "content_block_tool_result":
        content = message.get("content")
        if not isinstance(content, list) or record.block_index is None:
            return
        if record.block_index >= len(content) or not isinstance(content[record.block_index], dict):
            return
        content[record.block_index]["content"] = copy.deepcopy(payload)
        return

    if record.location_type == "role_tool_content":
        message["content"] = copy.deepcopy(payload)
        return

    if record.location_type == "parts_function_response":
        parts = message.get("parts")
        if not isinstance(parts, list) or record.part_index is None:
            return
        if record.part_index >= len(parts) or not isinstance(parts[record.part_index], dict):
            return
        function_response = parts[record.part_index].get("function_response")
        if isinstance(function_response, dict):
            function_response["response"] = copy.deepcopy(payload)


def _estimate_pressure(messages: list[dict[str, Any]], config: MidRunMicrocompactConfig, max_tokens: int) -> _Pressure:
    source_chars = _message_chars(messages)
    estimated_tokens = int(math.ceil(source_chars / _TOKEN_CHARS)) if source_chars else 0
    context_window = max(0, int(max_tokens or 0))
    if context_window <= 0:
        return _Pressure(
            source_chars=source_chars,
            estimated_tokens=estimated_tokens,
            max_context_window_tokens=context_window,
            context_ratio=0.0,
            remaining_tokens=0,
            triggered_by_context_ratio=False,
            triggered_by_remaining_tokens=False,
        )

    context_ratio = estimated_tokens / context_window
    remaining_tokens = context_window - estimated_tokens
    return _Pressure(
        source_chars=source_chars,
        estimated_tokens=estimated_tokens,
        max_context_window_tokens=context_window,
        context_ratio=context_ratio,
        remaining_tokens=remaining_tokens,
        triggered_by_context_ratio=context_ratio >= float(config.trigger_context_ratio),
        triggered_by_remaining_tokens=remaining_tokens <= int(config.trigger_remaining_tokens),
    )


def _select_candidate_identities(
    messages: list[dict[str, Any]],
    records: list[_ResultRecord],
    *,
    config: MidRunMicrocompactConfig,
    current_call_ids: set[str],
) -> tuple[set[tuple[Any, ...]], int]:
    keep_count = max(0, int(config.keep_recent_completed_turns))
    protected: set[tuple[Any, ...]] = set()
    protect_latest_current_group = (
        not config.compact_current_batch
        and bool(current_call_ids)
    )
    completed_turns: list[set[tuple[Any, ...]]] = []
    if keep_count or protect_latest_current_group:
        completed_turns = _completed_tool_turn_identities(messages, records)

    if keep_count:
        for turn in completed_turns[-keep_count:]:
            protected.update(turn)
    if protect_latest_current_group and completed_turns:
        protected.update(completed_turns[-1])

    candidate_identities: set[tuple[Any, ...]] = set()
    estimated_savings = 0

    for record in records:
        if record.identity in protected:
            continue
        if (
            not config.compact_current_batch
            and record.call_id
            and record.call_id in current_call_ids
        ):
            continue
        if _is_compacted_payload(record.payload):
            continue
        possible_savings = record.original_chars - int(config.max_compacted_result_chars)
        if possible_savings <= 0:
            continue
        candidate_identities.add(record.identity)
        estimated_savings += possible_savings

    return candidate_identities, max(0, estimated_savings)


def _compact_record(
    *,
    record: _ResultRecord,
    provider: str,
    toolkit: Toolkit | None,
    session_id: str,
    latest_messages: list[dict[str, Any]],
    config: MidRunMicrocompactConfig,
) -> tuple[Any, int, int, int] | None:
    synthetic = _synthetic_message(record)
    if not synthetic:
        return None

    tool_call = ToolCall(
        call_id=record.call_id,
        name=record.tool_name,
        arguments={},
    )
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=max(1, int(config.max_compacted_result_chars)),
            max_batch_chars=max(1, int(config.max_compacted_result_chars)),
            preview_chars=max(0, int(config.preview_chars)),
            min_chars_to_budget=0,
        )
    )
    outcome = controller.budget_messages(
        provider=provider,
        toolkit=toolkit,
        tool_calls=[tool_call],
        result_messages=[synthetic],
        session_id=session_id,
        latest_messages=latest_messages,
        reason="mid_run_microcompact",
    )
    if outcome.stats.compacted_count <= 0 or not outcome.messages:
        return None
    payload = _synthetic_payload(record, outcome.messages[0])
    if _stable_json_dumps(payload) == _stable_json_dumps(record.payload):
        return None
    return (
        payload,
        int(outcome.stats.optimizer_error_count),
        int(outcome.stats.budgeted_chars),
        int(outcome.stats.saved_chars),
    )


def _compact_messages(
    messages: list[dict[str, Any]],
    *,
    candidate_identities: set[tuple[Any, ...]],
    provider: str,
    event_tool_calls: list[ToolCall],
    toolkit: Toolkit | None,
    session_id: str,
    latest_messages: list[dict[str, Any]],
    config: MidRunMicrocompactConfig,
) -> _CompactMessagesOutcome:
    compacted_messages = copy.deepcopy(messages)
    records = _collect_result_records(compacted_messages, tool_calls=event_tool_calls)
    result_count = 0
    compacted_count = 0
    optimizer_error_count = 0
    original_chars = 0
    budgeted_chars = 0
    saved_chars = 0

    for record in records:
        if record.identity not in candidate_identities:
            continue
        result_count += 1
        original_chars += record.original_chars
        compacted = _compact_record(
            record=record,
            provider=provider,
            toolkit=toolkit,
            session_id=session_id,
            latest_messages=latest_messages,
            config=config,
        )
        if compacted is None:
            budgeted_chars += record.original_chars
            continue

        payload, errors, record_budgeted_chars, record_saved_chars = compacted
        _write_record_payload(compacted_messages, record, payload)
        compacted_count += 1
        optimizer_error_count += errors
        budgeted_chars += max(0, record_budgeted_chars)
        saved_chars += max(0, record_saved_chars)

    return _CompactMessagesOutcome(
        messages=compacted_messages,
        result_count=result_count,
        compacted_count=compacted_count,
        optimizer_error_count=optimizer_error_count,
        original_chars=original_chars,
        budgeted_chars=budgeted_chars,
        saved_chars=saved_chars,
    )


@dataclass
class MidRunMicrocompactHarness(BaseRuntimeHarness):
    name: str = "mid_run_microcompact"
    phases: tuple[RuntimePhase, ...] = ("after_tool_batch",)
    order: int = 120
    config: MidRunMicrocompactConfig = field(default_factory=MidRunMicrocompactConfig)

    def build_delta(self, context: HarnessContext) -> HarnessDelta | None:
        config = MidRunMicrocompactConfig.from_raw(self.config)
        if not config.enabled:
            return None

        state = context.state
        transcript = [
            copy.deepcopy(message)
            for message in state.transcript
            if isinstance(message, dict)
        ]
        if not transcript:
            return None

        event_tool_calls = _event_tool_calls(context.event)
        current_call_ids = {
            call.call_id
            for call in event_tool_calls
            if call.call_id
        }
        records = _collect_result_records(transcript, tool_calls=event_tool_calls)
        if not records:
            return None

        candidate_identities, estimated_candidate_savings = _select_candidate_identities(
            transcript,
            records,
            config=config,
            current_call_ids=current_call_ids,
        )
        if not candidate_identities:
            return None

        source_messages = (
            state.next_model_input
            if isinstance(state.next_model_input, list)
            else state.transcript
        )
        pressure = _estimate_pressure(
            [message for message in source_messages if isinstance(message, dict)],
            config,
            state.provider_state.max_context_window_tokens,
        )
        if not pressure.triggered and estimated_candidate_savings < int(config.min_savings_chars):
            return None

        raw_toolkit = context.event.get("toolkit")
        toolkit = raw_toolkit if isinstance(raw_toolkit, Toolkit) else None
        provider = str(state.provider_state.provider or "").strip().lower()
        session_id = str(state.session_state.session_id or "")
        latest_messages = context.latest_messages() or transcript

        transcript_outcome = _compact_messages(
            transcript,
            candidate_identities=candidate_identities,
            provider=provider,
            event_tool_calls=event_tool_calls,
            toolkit=toolkit,
            session_id=session_id,
            latest_messages=latest_messages,
            config=config,
        )
        if (
            transcript_outcome.compacted_count <= 0
            or transcript_outcome.saved_chars < int(config.min_savings_chars)
        ):
            return None

        state_updates: dict[str, Any] = {
            "transcript": transcript_outcome.messages,
        }
        next_input_outcome: _CompactMessagesOutcome | None = None
        if isinstance(state.next_model_input, list):
            next_input_outcome = _compact_messages(
                state.next_model_input,
                candidate_identities=candidate_identities,
                provider=provider,
                event_tool_calls=event_tool_calls,
                toolkit=toolkit,
                session_id=session_id,
                latest_messages=latest_messages,
                config=config,
            )
            state_updates["next_model_input"] = next_input_outcome.messages

        stats = {
            "result_count": transcript_outcome.result_count,
            "candidate_count": len(candidate_identities),
            "compacted_count": transcript_outcome.compacted_count,
            "optimizer_error_count": transcript_outcome.optimizer_error_count,
            "original_chars": transcript_outcome.original_chars,
            "budgeted_chars": transcript_outcome.budgeted_chars,
            "saved_chars": transcript_outcome.saved_chars,
            "estimated_source_chars": pressure.source_chars,
            "estimated_source_tokens": pressure.estimated_tokens,
            "max_context_window_tokens": pressure.max_context_window_tokens,
            "context_ratio": pressure.context_ratio,
            "remaining_tokens": pressure.remaining_tokens,
            "triggered_by_context_ratio": pressure.triggered_by_context_ratio,
            "triggered_by_remaining_tokens": pressure.triggered_by_remaining_tokens,
            "triggered_by_large_history_savings": estimated_candidate_savings >= int(config.min_savings_chars),
        }
        state_updates["optimizer_state"] = {self.name: stats}

        trace = {
            "harness": self.name,
            "candidate_count": len(candidate_identities),
            "compacted_count": transcript_outcome.compacted_count,
            "saved_chars": transcript_outcome.saved_chars,
            "estimated_candidate_savings": estimated_candidate_savings,
            "source_chars": pressure.source_chars,
            "estimated_source_tokens": pressure.estimated_tokens,
            "context_ratio": pressure.context_ratio,
            "remaining_tokens": pressure.remaining_tokens,
            "next_model_input_compacted_count": (
                next_input_outcome.compacted_count
                if next_input_outcome is not None
                else 0
            ),
        }
        ops = ()
        if state.latest_version_id is not None:
            active_messages = context.latest_messages()
            replace_end = len(active_messages) if active_messages else len(transcript)
            ops = (
                ReplaceSpanOp(
                    start=0,
                    end=replace_end,
                    messages=transcript_outcome.messages,
                ),
            )

        return HarnessDelta(
            created_by=f"harness.{self.name}",
            ops=ops,
            state_updates=state_updates,
            trace=trace,
        )


__all__ = [
    "MidRunMicrocompactConfig",
    "MidRunMicrocompactHarness",
]
