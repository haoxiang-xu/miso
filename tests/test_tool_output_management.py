from __future__ import annotations

import hashlib

import pytest

from unchain.tools.output_management import (
    TOOL_OUTPUT_MANAGEMENT_SCHEMA,
    TOOL_OUTPUT_POLICY_MAP_SCHEMA,
    ToolOutputManagementError,
    ToolOutputManager,
    ToolOutputPolicyVersionError,
    ToolOutputReadError,
)
from unchain.kernel.types import ToolCall
from unchain.tools import Toolkit, get_provider_message_builder
from unchain.tools.tool import Tool


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_active_manager_projects_once_and_disables_legacy_budget():
    manager = ToolOutputManager.active_default(
        attempt_id="attempt-a",
        preview_chars=4,
        inline_chars=8,
    )
    raw = b"0123456789"
    receipt = manager.project(
        raw,
        full_output_ref={"uri": "pupu://artifact/a@1"},
        digest=_digest(raw),
        content_bytes=len(raw),
        call_id="call-a",
    )

    assert manager.legacy_budget_enabled is False
    assert receipt.payload["projection"] == "default"
    assert receipt.payload["inline"] is False
    assert receipt.payload["preview"] == "0123"
    assert receipt.metadata["projection_version"] == "v1"
    assert manager.project(
        raw,
        full_output_ref={"uri": "pupu://artifact/a@1"},
        digest=_digest(raw),
        content_bytes=len(raw),
        call_id="call-a",
    ) == receipt


def test_snapshot_is_closed_and_unknown_explicit_policy_fails_closed():
    manager = ToolOutputManager.active_default()
    snapshot = manager.runtime_snapshot()
    assert snapshot["schema"] == TOOL_OUTPUT_MANAGEMENT_SCHEMA
    assert ToolOutputManager.from_runtime_config(
        {"tool_output_management": snapshot}
    ).legacy_budget_enabled is False

    raw = b"value"
    with pytest.raises(ToolOutputPolicyVersionError):
        manager.project(
            raw,
            full_output_ref={"uri": "pupu://artifact/a@1"},
            digest=_digest(raw),
            content_bytes=len(raw),
            requested_policy="does-not-exist",
        )

    invalid = dict(snapshot)
    invalid["unexpected"] = True
    with pytest.raises(ToolOutputManagementError):
        ToolOutputManager.from_runtime_config({"tool_output_management": invalid})


def test_closed_tool_policy_map_selects_each_declared_tool_policy():
    manager = ToolOutputManager.active_default()
    config = {
        "tool_output_policy_map": {
            "schema": TOOL_OUTPUT_POLICY_MAP_SCHEMA,
            "policies": {"large_search": "artifact_only"},
        }
    }

    assert manager.resolve_policy_for_tool(
        config, tool_name="large_search"
    ).name == "artifact_only"
    assert manager.resolve_policy_for_tool(config, tool_name="small_read").name == "default"

    with pytest.raises(ToolOutputManagementError):
        manager.resolve_policy_for_tool(
            {"tool_output_policy_map": {"schema": "invalid", "policies": {}}},
            tool_name="large_search",
        )


def test_active_runtime_requires_native_tool_output_policy_declarations():
    class UndeclaredTool:
        pass

    class Toolkit:
        tools = {"large_search": UndeclaredTool()}

    config = ToolOutputManager.active_runtime_config_for_toolkit(
        Toolkit(),
        attempt_id="attempt-toolkit-policy",
    )
    manager = ToolOutputManager.from_runtime_config(config)

    assert manager.resolve_policy_for_tool(
        config,
        tool_name="large_search",
    ).name == "default"
    assert "tool_output_policy_map" not in config


@pytest.mark.parametrize("provider", ("openai", "anthropic", "hyperspace", "ollama"))
def test_declared_policy_projects_a_provider_valid_result_for_each_provider(
    provider: str,
):
    """Exercise provider-native encoding of a sealed Toolkit projection.

    Real normal, graph, resume, and subagent entrypoints are covered by the
    provider-boundary tests; this test is intentionally limited to the common
    provider-message builder contract.
    """
    toolkit = Toolkit(
        {
            "large_search": Tool(
                name="large_search",
                description="search",
                func=lambda: None,
                output_policy="artifact_only",
            )
        }
    )
    config = ToolOutputManager.active_runtime_config_for_toolkit(
        toolkit,
        attempt_id=f"provider-projection-{provider}",
    )
    manager = ToolOutputManager.from_runtime_config(
        config,
        attempt_id=f"provider-projection-{provider}",
    )
    raw = b"result that is intentionally not model-inline"
    projection = manager.project(
        raw,
        full_output_ref={"uri": "pupu://artifact/tool-output@1"},
        digest=_digest(raw),
        content_bytes=len(raw),
        call_id="call-output",
        requested_policy=manager.resolve_policy_for_tool(
            config,
            tool_name="large_search",
        ).name,
    )

    assert projection.payload["projection"] == "artifact_only"
    assert projection.payload["full_output_ref"] == {
        "uri": "pupu://artifact/tool-output@1"
    }
    assert projection.payload["content_sha256"] == _digest(raw)
    assert projection.payload["content_bytes"] == len(raw)
    messages = get_provider_message_builder(provider).build_tool_result_messages(
        tool_call=ToolCall(
            call_id="call-output",
            name="large_search",
            arguments={},
        ),
        tool_result=projection.payload,
    )
    assert messages
    assert "intentionally not model-inline" not in str(messages)
    assert "pupu://artifact/tool-output@1" in str(messages)


@pytest.mark.parametrize("source_ref", (None, {}, "", 0))
def test_invalid_source_receipt_fails_closed(source_ref):
    manager = ToolOutputManager.active_default()
    raw = b"value"

    with pytest.raises(ToolOutputManagementError):
        manager.project(
            raw,
            full_output_ref=source_ref,
            digest=_digest(raw),
            content_bytes=len(raw),
        )


def test_page_continuation_cannot_switch_source_artifact():
    manager = ToolOutputManager.active_default()
    first = manager.read_page(
        source_ref={"uri": "pupu://artifact/a@1"}, offset=0, limit=10
    )
    next_page = manager.read_page(
        source_ref={"uri": "pupu://artifact/a@1"},
        offset=10,
        limit=10,
        continuation=first,
    )
    assert next_page.offset == 10
    with pytest.raises(ToolOutputReadError):
        manager.read_page(
            source_ref={"uri": "pupu://artifact/b@1"},
            offset=10,
            limit=10,
            continuation=first,
        )


def test_manager_owns_provider_valid_historical_compaction():
    manager = ToolOutputManager.active_default()
    openai = manager.compact_historical_message(
        {"type": "function_call_output", "call_id": "call-a", "output": "raw"},
        call_ids={"call-a"},
    )
    assert openai["output"] == (
        '{"memory_v2_compacted": true, "call_ids": ["call-a"], '
        '"note": "Full tool output is available in the durable context journal."}'
    )

    gemini = manager.compact_historical_message(
        {
            "role": "user",
            "parts": [
                {"function_response": {"name": "search", "response": {"raw": True}}}
            ],
        },
        call_ids={"call-a"},
    )
    assert gemini["parts"][0]["function_response"]["response"] == {
        "memory_v2_compacted": True,
        "call_ids": ["call-a"],
    }
