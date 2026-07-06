from __future__ import annotations

import json
import queue
from typing import Any

from unchain.kernel.types import ToolCall
from unchain.tools.messages import get_provider_message_builder
from unchain.tools.models import ToolHistoryOptimizationContext
from unchain.tools.result_budget import (
    ToolResultBudgetConfig,
    ToolResultBudgetController,
)
from unchain.tools.toolkit import Toolkit


def _call(call_id: str = "call_1", name: str = "demo_tool") -> ToolCall:
    return ToolCall(call_id=call_id, name=name, arguments={})


def _message(provider: str, call: ToolCall, result: dict[str, Any]) -> dict[str, Any]:
    return get_provider_message_builder(provider).build_tool_result_message(
        tool_call=call,
        tool_result=result,
    )


def _openai_payload(message: dict[str, Any]) -> dict[str, Any]:
    return json.loads(message["output"])


def _provider_payload(
    provider: str,
    message: dict[str, Any],
    call: ToolCall,
) -> dict[str, Any]:
    if provider in {"anthropic", "hyperspace"}:
        assert message["role"] == "user"
        assert len(message["content"]) == 1
        content = message["content"][0]
        assert content["type"] == "tool_result"
        assert content["tool_use_id"] == call.call_id
        return json.loads(content["content"])

    if provider == "ollama":
        assert message["role"] == "tool"
        assert message["tool_call_id"] == call.call_id
        return json.loads(message["content"])

    if provider == "gemini":
        assert message["role"] == "user"
        assert len(message["parts"]) == 1
        function_response = message["parts"][0]["function_response"]
        assert function_response["name"] == call.name
        payload = function_response["response"]
        assert isinstance(payload, dict)
        return payload

    raise AssertionError(f"unsupported provider for payload parsing: {provider}")


def test_budget_config_from_raw_accepts_config_dict_and_non_dict():
    existing = ToolResultBudgetConfig(max_result_chars=20)

    assert ToolResultBudgetConfig.from_raw(existing) is existing
    assert ToolResultBudgetConfig.from_raw(None) == ToolResultBudgetConfig()

    parsed = ToolResultBudgetConfig.from_raw(
        {
            "max_result_chars": "0",
            "max_batch_chars": "-5",
            "preview_chars": "-10",
            "min_chars_to_budget": "-20",
            "ignored": 999,
        }
    )
    assert parsed.max_result_chars == 1
    assert parsed.max_batch_chars == 1
    assert parsed.preview_chars == 0
    assert parsed.min_chars_to_budget == 0


def test_budget_controller_compacts_large_openai_result_with_digest():
    call = _call()
    message = _message("openai", call, {"blob": "A" * 600})
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=160,
            max_batch_chars=1_000,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=Toolkit(),
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
    )

    payload = _openai_payload(outcome.messages[0])
    assert payload["compacted"] is True
    assert payload["reason"] == "tool_result_budget"
    assert payload["tool_name"] == "demo_tool"
    assert payload["call_id"] == "call_1"
    assert payload["original_chars"] > payload["budgeted_chars"]
    assert isinstance(payload["original_sha1"], str)
    assert len(payload["original_sha1"]) == 40
    assert "preview" in payload
    assert outcome.stats.result_count == 1
    assert outcome.stats.compacted_count == 1
    assert outcome.stats.saved_chars > 0


def test_budget_controller_prefers_per_tool_result_optimizer():
    call = _call(name="semantic_tool")
    toolkit = Toolkit()

    def semantic_tool() -> dict[str, Any]:
        return {"blob": "unused"}

    def optimizer(payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        assert context.tool_name == "semantic_tool"
        assert context.call_id == "call_1"
        assert context.kind == "result"
        return {
            "semantic_compacted": True,
            "first_chars": str(payload["blob"])[:5],
        }

    toolkit.register(
        semantic_tool,
        name="semantic_tool",
        history_result_optimizer=optimizer,
    )
    message = _message("openai", call, {"blob": "B" * 600})
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=180,
            max_batch_chars=1_000,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=toolkit,
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
    )

    payload = _openai_payload(outcome.messages[0])
    assert payload["semantic_compacted"] is True
    assert payload["first_chars"] == "BBBBB"
    assert outcome.stats.compacted_count == 1
    assert outcome.stats.optimizer_error_count == 0


def test_budget_controller_falls_back_when_optimizer_raises():
    call = _call(name="fragile_tool")
    toolkit = Toolkit()

    def fragile_tool() -> dict[str, Any]:
        return {"blob": "unused"}

    def optimizer(payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        raise RuntimeError("optimizer failed")

    toolkit.register(
        fragile_tool,
        name="fragile_tool",
        history_result_optimizer=optimizer,
    )
    message = _message("openai", call, {"blob": "C" * 600})
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=160,
            max_batch_chars=1_000,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=toolkit,
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
    )

    payload = _openai_payload(outcome.messages[0])
    assert payload["compacted"] is True
    assert payload["reason"] == "tool_result_budget"
    assert outcome.stats.optimizer_error_count == 1


def test_budget_controller_supports_anthropic_ollama_and_gemini_result_shapes():
    providers = ["anthropic", "hyperspace", "ollama", "gemini"]
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=160,
            max_batch_chars=1_000,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    for provider in providers:
        call = _call(call_id=f"call_{provider}")
        message = _message(provider, call, {"blob": provider * 120})
        outcome = controller.budget_messages(
            provider=provider,
            toolkit=Toolkit(),
            tool_calls=[call],
            result_messages=[message],
            session_id="session-a",
        )

        assert outcome.stats.compacted_count == 1
        payload = _provider_payload(provider, outcome.messages[0], call)
        assert payload["compacted"] is True
        assert payload["reason"] == "tool_result_budget"
        assert payload["call_id"] == f"call_{provider}"


def test_budget_controller_enforces_batch_budget_on_largest_results():
    batch_cap = 640
    calls = [
        _call(call_id="call_1", name="tool_a"),
        _call(call_id="call_2", name="tool_b"),
    ]
    smaller_payload = {"blob": "A" * 180}
    larger_payload = {"blob": "B" * 520}
    messages = [
        _message("openai", calls[0], smaller_payload),
        _message("openai", calls[1], larger_payload),
    ]
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=1_000,
            max_batch_chars=batch_cap,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=Toolkit(),
        tool_calls=calls,
        result_messages=messages,
        session_id="session-a",
    )

    payloads = [_openai_payload(message) for message in outcome.messages]
    assert payloads[0] == smaller_payload
    assert payloads[1]["compacted"] is True
    assert payloads[1]["reason"] == "tool_result_budget"
    assert payloads[1]["tool_name"] == "tool_b"
    assert payloads[1]["call_id"] == "call_2"
    assert payloads[1]["original_chars"] > payloads[1]["budgeted_chars"]
    assert outcome.stats.compacted_count == 1
    assert outcome.stats.budgeted_chars <= batch_cap
    assert outcome.stats.budgeted_chars <= outcome.stats.original_chars
    assert outcome.stats.saved_chars > 0


def test_budget_controller_preserves_small_result_exactly():
    call = _call()
    message = _message("openai", call, {"value": 2})
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=160,
            max_batch_chars=1_000,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=Toolkit(),
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
    )

    assert outcome.messages == [message]
    assert outcome.stats.compacted_count == 0
    assert outcome.stats.saved_chars == 0


def test_budget_controller_does_not_copy_latest_messages_for_small_result_without_optimizer():
    call = _call()
    message = _message("openai", call, {"value": 2})

    outcome = ToolResultBudgetController().budget_messages(
        provider="openai",
        toolkit=Toolkit(),
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
        latest_messages=[
            {
                "role": "assistant",
                "content": queue.SimpleQueue(),
            }
        ],
    )

    assert outcome.messages == [message]
    assert outcome.stats.compacted_count == 0
    assert outcome.stats.saved_chars == 0


def test_budget_controller_handles_gemini_native_payload_with_mixed_keys():
    call = _call(call_id="call_gemini_mixed", name="gemini_mixed_tool")
    message = {
        "role": "user",
        "parts": [
            {
                "function_response": {
                    "name": call.name,
                    "response": {
                        1: "integer key",
                        "items": {"beta", "alpha"},
                        ("tuple", 2): {"blob": "G" * 600},
                    },
                }
            }
        ],
    }
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=160,
            max_batch_chars=1_000,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    outcome = controller.budget_messages(
        provider="gemini",
        toolkit=Toolkit(),
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
    )

    payload = _provider_payload("gemini", outcome.messages[0], call)
    assert payload["compacted"] is True
    assert payload["call_id"] == "call_gemini_mixed"
    assert isinstance(payload["original_sha1"], str)
    assert len(payload["original_sha1"]) == 40


def test_budget_controller_enforces_batch_budget_below_min_chars():
    batch_cap = 430
    calls = [
        _call(call_id="call_medium_1", name="tool_a"),
        _call(call_id="call_medium_2", name="tool_b"),
    ]
    messages = [
        _message("openai", calls[0], {"blob": "A" * 220}),
        _message("openai", calls[1], {"blob": "B" * 220}),
    ]
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=1_000,
            max_batch_chars=batch_cap,
            preview_chars=16,
            min_chars_to_budget=500,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=Toolkit(),
        tool_calls=calls,
        result_messages=messages,
        session_id="session-a",
    )

    assert outcome.stats.compacted_count > 0
    assert outcome.stats.budgeted_chars <= batch_cap
    assert outcome.stats.saved_chars > 0


def test_budget_controller_skips_no_savings_compaction_when_not_batch_required():
    call = _call()
    payload = {"value": "x" * 20}
    message = _message("openai", call, payload)
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=10,
            max_batch_chars=10_000,
            preview_chars=1_000,
            min_chars_to_budget=1,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=Toolkit(),
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
    )

    assert outcome.messages == [message]
    assert outcome.stats.compacted_count == 0
    assert outcome.stats.saved_chars == 0


def test_budget_controller_preserves_raw_openai_string_writeback():
    call = _call(name="raw_tool")
    toolkit = Toolkit()

    def raw_tool() -> dict[str, Any]:
        return {"value": "unused"}

    def optimizer(payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        assert isinstance(payload, str)
        return f"raw summary for {context.call_id}"

    toolkit.register(
        raw_tool,
        name="raw_tool",
        history_result_optimizer=optimizer,
    )
    message = {
        "type": "function_call_output",
        "call_id": call.call_id,
        "output": "RAW-" + ("x" * 600),
    }
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=160,
            max_batch_chars=1_000,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=toolkit,
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
    )

    assert outcome.messages[0]["output"] == "raw summary for call_1"
    assert outcome.stats.compacted_count == 1


def test_budget_controller_falls_back_when_optimizer_has_no_savings():
    call = _call(name="no_savings_tool")
    toolkit = Toolkit()

    def no_savings_tool() -> dict[str, Any]:
        return {"blob": "unused"}

    def optimizer(payload: Any, context: ToolHistoryOptimizationContext) -> Any:
        assert context.tool_name == "no_savings_tool"
        return payload

    toolkit.register(
        no_savings_tool,
        name="no_savings_tool",
        history_result_optimizer=optimizer,
    )
    message = _message("openai", call, {"blob": "D" * 600})
    controller = ToolResultBudgetController(
        ToolResultBudgetConfig(
            max_result_chars=160,
            max_batch_chars=1_000,
            preview_chars=24,
            min_chars_to_budget=40,
        )
    )

    outcome = controller.budget_messages(
        provider="openai",
        toolkit=toolkit,
        tool_calls=[call],
        result_messages=[message],
        session_id="session-a",
    )

    payload = _openai_payload(outcome.messages[0])
    assert payload["compacted"] is True
    assert payload["reason"] == "tool_result_budget"
    assert payload["tool_name"] == "no_savings_tool"
    assert outcome.stats.compacted_count == 1
