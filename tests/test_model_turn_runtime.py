from __future__ import annotations

import json

import pytest

from unchain.kernel import ModelTurnResult, ToolCall
from unchain.kernel.provider_replay import (
    current_provider_replay_frame,
    merge_model_turn_replay_frame,
    set_provider_replay_frame,
)
from unchain.kernel.provider_replay import tool_schema_digest, tool_schema_manifest
from unchain.kernel.state import RunState
from unchain.retry import RetryConfig
from unchain.tools import Toolkit


class _RecordingModelIO:
    def __init__(self, result: ModelTurnResult) -> None:
        self.result = result
        self.requests = []

    def fetch_turn(self, request):
        self.requests.append(request)
        return self.result


def _message_count(messages, *, role, content):
    return sum(
        1
        for message in messages
        if message.get("role") == role and message.get("content") == content
    )


def _replay_case(provider):
    if provider == "openai":
        return (
            "openai.responses.v1",
            [
                {"role": "user", "content": "u1"},
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "opaque-ciphertext",
                    "summary": [],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "a1"}],
                },
            ],
            lambda messages: next(
                item for item in messages if item.get("type") == "reasoning"
            )["encrypted_content"]
            == "opaque-ciphertext",
        )
    if provider in {"anthropic", "hyperspace"}:
        return (
            "anthropic.messages.v1",
            [
                {"role": "user", "content": "u1"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "plan",
                            "signature": "opaque-signature",
                        },
                        {"type": "text", "text": "a1"},
                    ],
                },
            ],
            lambda messages: messages[1]["content"][0]["signature"]
            == "opaque-signature",
        )
    return (
        "ollama.chat.v1",
        [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1", "thinking": "plan"},
        ],
        lambda messages: messages[1]["thinking"] == "plan",
    )


@pytest.mark.parametrize("provider", ["openai", "anthropic", "hyperspace", "ollama"])
def test_complete_provider_replay_is_reconciled_with_current_model_context(provider):
    from unchain.providers.model_turn_runtime import build_model_turn_request

    replay_format, replay_items, opaque_assertion = _replay_case(provider)
    state = RunState()
    state.seed_messages(
        [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]
    )
    state.provider_state.provider = provider
    set_provider_replay_frame(
        state,
        {
            "format": replay_format,
            "complete": True,
            "items": replay_items,
        },
    )

    request = build_model_turn_request(state)

    assert _message_count(request.messages, role="user", content="u1") == 1
    assert _message_count(request.messages, role="user", content="u2") == 1
    assert opaque_assertion(request.messages)


def test_provider_context_replaces_stale_system_contribution_instead_of_appending():
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(
        [
            {"role": "system", "content": "current tools guidance"},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
        ]
    )
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {"role": "system", "content": "stale tools guidance"},
                {"role": "user", "content": "u1"},
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "opaque-ciphertext",
                    "summary": [],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "a1"}],
                },
            ],
        },
    )

    request = build_model_turn_request(state)

    assert _message_count(
        request.messages,
        role="system",
        content="current tools guidance",
    ) == 1
    assert _message_count(
        request.messages,
        role="system",
        content="stale tools guidance",
    ) == 0
    assert next(
        item for item in request.messages if item.get("type") == "reasoning"
    )["encrypted_content"] == "opaque-ciphertext"


def test_provider_context_honors_destructive_compaction_around_active_opaque_tool_turn():
    from unchain.providers.model_turn_runtime import build_model_turn_request

    compacted_output = '{"summary":"large result removed"}'
    state = RunState()
    state.seed_messages(
        [
            {"role": "system", "content": "[CONTEXT SUMMARY] old turn summarized"},
            {"role": "user", "content": "active"},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "demo_tool",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": compacted_output,
            },
        ]
    )
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {"role": "user", "content": "deleted old turn"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "deleted answer"}],
                },
                {"role": "user", "content": "active"},
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "opaque-ciphertext",
                    "summary": [],
                },
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "demo_tool",
                    "arguments": "{}",
                    "status": "completed",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "very large old result",
                },
            ],
        },
    )

    request = build_model_turn_request(state)

    assert all(item.get("content") != "deleted old turn" for item in request.messages)
    assert all(item.get("output") != "very large old result" for item in request.messages)
    assert request.messages[-1]["output"] == compacted_output
    assert request.messages[-3]["encrypted_content"] == "opaque-ciphertext"
    assert request.messages[-2]["id"] == "fc_1"


def test_provider_context_fails_closed_when_opaque_tool_call_is_mutated():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(
        [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "demo_tool",
                "arguments": '{"changed":true}',
            }
        ]
    )
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "opaque-ciphertext",
                    "summary": [],
                },
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "demo_tool",
                    "arguments": "{}",
                    "status": "completed",
                },
            ],
        },
    )

    with pytest.raises(ProviderContextProjectionError, match="mutated ambiguously"):
        build_model_turn_request(state)


def test_previous_response_request_keeps_delta_and_builds_complete_local_fallback():
    from unchain.providers.model_turn_runtime import build_model_turn_request

    tool_output = {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"value":2}',
    }
    state = RunState()
    state.seed_messages(
        [
            {"role": "user", "content": "u1"},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "demo_tool",
                "arguments": "{}",
            },
            tool_output,
        ]
    )
    state.provider_state.provider = "openai"
    state.provider_state.use_previous_response_chain = True
    state.provider_state.previous_response_id = "resp_1"
    state.remote_continuation_input = [tool_output]
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {"role": "user", "content": "u1"},
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "encrypted_content": "opaque-ciphertext",
                    "summary": [],
                },
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "demo_tool",
                    "arguments": "{}",
                    "status": "completed",
                },
                tool_output,
            ],
        },
    )

    request = build_model_turn_request(state)

    assert request.context_mode == "remote_continuation"
    assert request.previous_response_id == "resp_1"
    assert request.messages == [tool_output]
    assert request.fallback_messages is not None
    assert [item.get("type") for item in request.fallback_messages][-3:] == [
        "reasoning",
        "function_call",
        "function_call_output",
    ]


def test_deleted_opaque_text_turn_is_not_reattached_to_equal_new_text():
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(
        [
            {"role": "user", "content": "new turn"},
            {"role": "assistant", "content": "same answer"},
        ]
    )
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {"role": "user", "content": "deleted old turn"},
                {
                    "type": "reasoning",
                    "id": "rs_old",
                    "encrypted_content": "must-not-move",
                    "summary": [],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same answer"}],
                },
                {"role": "user", "content": "new turn"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same answer"}],
                },
            ],
        },
    )

    request = build_model_turn_request(state)

    assert request.messages == state.latest_messages()
    assert all(item.get("type") != "reasoning" for item in request.messages)


def test_large_replay_projection_uses_bounded_memory():
    import tracemalloc

    from unchain.providers.model_turn_runtime import build_model_turn_request

    semantic_messages = []
    wire_items = []
    for index in range(500):
        user = {"role": "user", "content": f"u{index}"}
        semantic_messages.extend(
            [user, {"role": "assistant", "content": "ok"}]
        )
        wire_items.extend(
            [
                user,
                {
                    "type": "reasoning",
                    "id": f"rs_{index}",
                    "encrypted_content": f"opaque-{index}",
                    "summary": [],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "ok"}
                    ],
                },
            ]
        )

    state = RunState()
    state.seed_messages(semantic_messages)
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": wire_items,
        },
    )

    tracemalloc.start()
    try:
        request = build_model_turn_request(state)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(request.messages) == 1500
    assert peak_bytes < 24 * 1024 * 1024


def test_identical_repeated_turns_fail_closed_instead_of_moving_old_reasoning():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(
        [
            {"role": "user", "content": "same question"},
            {"role": "assistant", "content": "same answer"},
        ]
    )
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {"role": "user", "content": "same question"},
                {
                    "type": "reasoning",
                    "id": "rs_old",
                    "encrypted_content": "must-not-move",
                    "summary": [],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same answer"}],
                },
                {"role": "user", "content": "same question"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "same answer"}],
                },
            ],
        },
    )

    with pytest.raises(ProviderContextProjectionError, match="repeated turns"):
        build_model_turn_request(state)


def test_parallel_openai_calls_are_an_atomic_replay_group():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(
        [
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "tool_2",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call_2",
                "output": "two",
            },
        ]
    )
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {
                    "type": "reasoning",
                    "id": "rs_parallel",
                    "encrypted_content": "opaque-parallel",
                    "summary": [],
                },
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "call_id": "call_1",
                    "name": "tool_1",
                    "arguments": "{}",
                    "status": "completed",
                },
                {
                    "type": "function_call",
                    "id": "fc_2",
                    "call_id": "call_2",
                    "name": "tool_2",
                    "arguments": "{}",
                    "status": "completed",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "one",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_2",
                    "output": "two",
                },
            ],
        },
    )

    with pytest.raises(ProviderContextProjectionError, match="atomic replay group"):
        build_model_turn_request(state)


def test_anthropic_retained_thinking_requires_signature():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    semantic_tool_message = {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "demo", "input": {}}
        ],
    }
    state = RunState()
    state.seed_messages([semantic_tool_message])
    state.provider_state.provider = "anthropic"
    set_provider_replay_frame(
        state,
        {
            "format": "anthropic.messages.v1",
            "complete": True,
            "items": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "plan", "signature": ""},
                        *semantic_tool_message["content"],
                    ],
                }
            ],
        },
    )

    with pytest.raises(ProviderContextProjectionError, match="signature"):
        build_model_turn_request(state)


def test_anthropic_signed_parallel_tool_group_cannot_be_partially_retained():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t2", "name": "two", "input": {}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "t2", "content": "two"}
                ],
            },
        ]
    )
    state.provider_state.provider = "anthropic"
    set_provider_replay_frame(
        state,
        {
            "format": "anthropic.messages.v1",
            "complete": True,
            "items": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "plan", "signature": "signed"},
                        {"type": "tool_use", "id": "t1", "name": "one", "input": {}},
                        {"type": "tool_use", "id": "t2", "name": "two", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "one"},
                        {"type": "tool_result", "tool_use_id": "t2", "content": "two"},
                    ],
                },
            ],
        },
    )

    with pytest.raises(ProviderContextProjectionError, match="atomic group"):
        build_model_turn_request(state)


@pytest.mark.parametrize(
    "target_messages,error_match",
    [
        (
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "one", "input": {}},
                        {"type": "tool_use", "id": "t2", "name": "two", "input": {}},
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "one"}
                    ],
                },
            ],
            "exactly once",
        ),
        (
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "one"}
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "one", "input": {}}
                    ],
                },
            ],
            "before matching calls",
        ),
        (
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "one", "input": {}}
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "one"},
                        {"type": "tool_result", "tool_use_id": "t1", "content": "again"},
                    ],
                },
            ],
            "exactly once",
        ),
    ],
)
def test_anthropic_tool_batches_fail_closed_when_incomplete_or_reordered(
    target_messages,
    error_match,
):
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(target_messages)
    state.provider_state.provider = "anthropic"
    set_provider_replay_frame(
        state,
        {
            "format": "anthropic.messages.v1",
            "complete": True,
            "items": target_messages,
        },
    )

    with pytest.raises(ProviderContextProjectionError, match=error_match):
        build_model_turn_request(state)


@pytest.mark.parametrize(
    "target_messages,error_match",
    [
        (
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "one", "input": {}}
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "one"}
                    ],
                },
            ],
            "assistant role",
        ),
        (
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "t1", "name": "one", "input": {}}
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "prefix"},
                        {"type": "tool_result", "tool_use_id": "t1", "content": "one"},
                    ],
                },
            ],
            "must come first",
        ),
    ],
)
def test_anthropic_tool_blocks_reject_invalid_roles_and_block_order(
    target_messages,
    error_match,
):
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(target_messages)
    state.provider_state.provider = "anthropic"
    set_provider_replay_frame(
        state,
        {
            "format": "anthropic.messages.v1",
            "complete": True,
            "items": target_messages,
        },
    )

    with pytest.raises(ProviderContextProjectionError, match=error_match):
        build_model_turn_request(state)


def test_replay_tool_manifest_allows_additions_but_rejects_changed_existing_tool():
    from unchain.kernel.provider_replay import ProviderReplayFrameError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    original_toolkit = Toolkit()
    original_toolkit.register(lambda value: value, name="alpha")
    state = RunState()
    state.seed_messages([{"role": "user", "content": "u1"}])
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": state.latest_messages(),
            "tool_schema_digest": tool_schema_digest(original_toolkit, "openai"),
            "tool_schema_manifest": tool_schema_manifest(original_toolkit, "openai"),
        },
    )

    extended_toolkit = Toolkit()
    extended_toolkit.register(lambda value: value, name="alpha")
    extended_toolkit.register(lambda text: text, name="beta")
    request = build_model_turn_request(state, toolkit=extended_toolkit)
    assert request.messages == state.latest_messages()

    replacement_subset = Toolkit()
    replacement_subset.register(lambda text: text, name="beta")
    subset_request = build_model_turn_request(state, toolkit=replacement_subset)
    assert subset_request.messages == state.latest_messages()

    changed_toolkit = Toolkit()
    changed_toolkit.register(lambda value, note="": value, name="alpha")
    with pytest.raises(ProviderReplayFrameError, match="previously captured tools"):
        build_model_turn_request(state, toolkit=changed_toolkit)


def test_remote_replay_manifest_accumulates_dynamic_tool_subsets():
    state = RunState()
    state.seed_messages([{"role": "user", "content": "u1"}])
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": False,
            "items": state.latest_messages(),
            "tool_schema_manifest": {"alpha": "digest-alpha"},
        },
    )

    merged = merge_model_turn_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": False,
            "items": [{"role": "assistant", "content": "a1"}],
            "response_items": [{"role": "assistant", "content": "a1"}],
            "mode": "append_response",
            "tool_schema_manifest": {"beta": "digest-beta"},
        },
    )

    assert merged is not None
    assert merged["complete"] is False
    assert merged["tool_schema_manifest"] == {
        "alpha": "digest-alpha",
        "beta": "digest-beta",
    }


def test_first_remote_replay_fragment_preserves_tool_schema_history():
    from unchain.kernel.provider_replay import (
        ProviderReplayFrameError,
        ensure_replay_tool_schema_compatible,
    )

    alpha_toolkit = Toolkit()
    alpha_toolkit.register(lambda value: value, name="alpha")
    beta_toolkit = Toolkit()
    beta_toolkit.register(lambda text: text, name="beta")

    state = RunState()
    first = merge_model_turn_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": False,
            "items": [{"role": "assistant", "content": "a1"}],
            "response_items": [{"role": "assistant", "content": "a1"}],
            "mode": "append_response",
            "tool_schema_digest": tool_schema_digest(alpha_toolkit, "openai"),
            "tool_schema_manifest": tool_schema_manifest(alpha_toolkit, "openai"),
        },
    )
    assert first is not None
    assert set(first["tool_schema_manifest"]) == {"alpha"}
    set_provider_replay_frame(state, first)

    second = merge_model_turn_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": False,
            "items": [{"role": "assistant", "content": "a2"}],
            "response_items": [{"role": "assistant", "content": "a2"}],
            "mode": "append_response",
            "tool_schema_digest": tool_schema_digest(beta_toolkit, "openai"),
            "tool_schema_manifest": tool_schema_manifest(beta_toolkit, "openai"),
        },
    )
    assert second is not None
    assert set(second["tool_schema_manifest"]) == {"alpha", "beta"}

    changed_toolkit = Toolkit()
    changed_toolkit.register(lambda value, note="": value, name="alpha")
    changed_toolkit.register(lambda text: text, name="beta")
    with pytest.raises(ProviderReplayFrameError, match="previously captured tools"):
        ensure_replay_tool_schema_compatible(
            second,
            toolkit=changed_toolkit,
            provider="openai",
        )


def test_replace_replay_manifest_retains_historical_tool_identity():
    from unchain.kernel.provider_replay import ProviderReplayFrameError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    alpha_toolkit = Toolkit()
    alpha_toolkit.register(lambda value: value, name="alpha")
    beta_toolkit = Toolkit()
    beta_toolkit.register(lambda text: text, name="beta")

    state = RunState()
    state.seed_messages([{"role": "user", "content": "u1"}])
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": state.latest_messages(),
            "tool_schema_manifest": tool_schema_manifest(
                alpha_toolkit,
                "openai",
            ),
        },
    )

    merged = merge_model_turn_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": state.latest_messages(),
            "mode": "replace",
            "tool_schema_manifest": tool_schema_manifest(
                beta_toolkit,
                "openai",
            ),
        },
    )
    assert merged is not None
    assert set(merged["tool_schema_manifest"]) == {"alpha", "beta"}
    set_provider_replay_frame(state, merged)

    changed_toolkit = Toolkit()
    changed_toolkit.register(
        lambda value, note="": value,
        name="alpha",
    )
    changed_toolkit.register(lambda text: text, name="beta")
    with pytest.raises(ProviderReplayFrameError, match="previously captured tools"):
        build_model_turn_request(state, toolkit=changed_toolkit)


def test_external_previous_response_id_rejects_full_history_without_local_replay():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(
        [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new"},
        ]
    )
    state.provider_state.provider = "openai"
    state.provider_state.previous_response_id = "resp_external"
    state.provider_state.use_previous_response_chain = True

    with pytest.raises(ProviderContextProjectionError, match="new user delta"):
        build_model_turn_request(state)


def test_external_previous_response_id_rejects_detaching_dirty_local_context():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages([{"role": "user", "content": "new delta"}])
    state.provider_state.provider = "openai"
    state.provider_state.previous_response_id = "resp_external"
    state.provider_state.use_previous_response_chain = True
    state.next_model_input = [
        {"role": "system", "content": "dynamic local policy"},
        {"role": "user", "content": "new delta"},
    ]
    state.rebuild_working_version(state.next_model_input)

    with pytest.raises(
        ProviderContextProjectionError,
        match="without a complete local replay prefix",
    ):
        build_model_turn_request(state)


def test_incomplete_remote_replay_rejects_unrepresented_before_model_changes():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    call = {
        "type": "function_call",
        "call_id": "call_1",
        "name": "demo",
        "arguments": "{}",
    }
    result = {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "ok",
    }
    state = RunState()
    state.seed_messages(
        [
            {"role": "system", "content": "NEW dynamic instruction"},
            {"role": "user", "content": "u1"},
            call,
            result,
        ]
    )
    state.provider_state.provider = "openai"
    state.provider_state.previous_response_id = "resp_external"
    state.provider_state.use_previous_response_chain = True
    state.remote_continuation_input = [result]
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": False,
            "items": [
                {"role": "system", "content": "OLD instruction"},
                {"role": "user", "content": "u1"},
                call,
                result,
            ],
            "incomplete_reason": "external remote prefix",
        },
    )

    with pytest.raises(ProviderContextProjectionError, match="incomplete remote"):
        build_model_turn_request(state)


def test_custom_reasoning_turn_without_native_frame_invalidates_stale_complete_replay():
    from unchain.providers.model_turn_runtime import (
        apply_model_turn_result,
        fetch_model_turn,
    )

    turn = ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "a2"}],
        tool_calls=[],
        final_text="a2",
        reasoning_items=[{"type": "reasoning", "text": "new reasoning"}],
    )
    state = RunState()
    state.seed_messages([{"role": "user", "content": "u2"}])
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {"role": "user", "content": "u1"},
                {
                    "type": "reasoning",
                    "id": "rs_old",
                    "encrypted_content": "old-only",
                    "summary": [],
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "a1"}],
                },
            ],
        },
    )

    fetched = fetch_model_turn(
        model_io=_RecordingModelIO(turn),
        retry_config=RetryConfig(max_retries=0),
        state=state,
    )
    apply_model_turn_result(state, fetched)

    replay = current_provider_replay_frame(state)
    assert replay is not None
    assert replay["complete"] is False
    assert any(item.get("content") == "a2" for item in replay["items"])


def test_openai_builtin_call_output_is_retained_with_its_assistant_anchor():
    from unchain.providers.model_turn_runtime import build_model_turn_request

    state = RunState()
    state.seed_messages(
        [
            {"role": "user", "content": "search"},
            {"role": "assistant", "content": "found it"},
            {"role": "user", "content": "continue"},
        ]
    )
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {"role": "user", "content": "search"},
                {
                    "type": "web_search_call",
                    "id": "ws_1",
                    "status": "completed",
                },
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "found it"}],
                },
            ],
        },
    )

    request = build_model_turn_request(state)

    assert any(
        item.get("type") == "web_search_call" and item.get("id") == "ws_1"
        for item in request.messages
    )
    assert request.messages[-1] == {"role": "user", "content": "continue"}


def test_openai_semantic_function_call_arguments_are_normalized_to_json_string():
    from unchain.providers.model_turn_runtime import build_model_turn_request

    target = [
        {
            "type": "function_call",
            "call_id": "call_1",
            "name": "demo",
            "arguments": {"x": 1},
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": "ok",
        },
    ]
    state = RunState()
    state.seed_messages(target)
    state.provider_state.provider = "openai"
    set_provider_replay_frame(
        state,
        {
            "format": "openai.responses.v1",
            "complete": True,
            "items": [
                {**target[0], "arguments": '{"x":1}'},
                target[1],
            ],
        },
    )

    request = build_model_turn_request(state)

    call = next(item for item in request.messages if item.get("type") == "function_call")
    assert json.loads(call["arguments"]) == {"x": 1}


def test_ollama_tool_calls_reject_non_assistant_role():
    from unchain.providers.context_assembler import ProviderContextProjectionError
    from unchain.providers.model_turn_runtime import build_model_turn_request

    target = [
        {
            "role": "user",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "demo", "arguments": {}},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]
    state = RunState()
    state.seed_messages(target)
    state.provider_state.provider = "ollama"
    set_provider_replay_frame(
        state,
        {
            "format": "ollama.chat.v1",
            "complete": True,
            "items": target,
        },
    )

    with pytest.raises(ProviderContextProjectionError, match="assistant role"):
        build_model_turn_request(state)


@pytest.mark.parametrize(
    ("provider", "expected_format"),
    [
        ("openai", "openai.responses.v1"),
        ("anthropic", "anthropic.messages.v1"),
        ("hyperspace", "anthropic.messages.v1"),
        ("ollama", "ollama.chat.v1"),
    ],
)
def test_custom_model_adapter_fallback_synthesizes_tool_call_anchor(
    provider,
    expected_format,
):
    from unchain.providers.model_turn_runtime import fetch_model_turn

    state = RunState()
    state.seed_messages([{"role": "user", "content": "use tool"}])
    state.provider_state.provider = provider
    turn = ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": ""}],
        tool_calls=[
            ToolCall(
                call_id="call_1",
                name="demo",
                arguments='{"x":1}',
            )
        ],
        final_text="",
    )

    fetched = fetch_model_turn(
        model_io=_RecordingModelIO(turn),
        retry_config=RetryConfig(max_retries=0),
        state=state,
    )

    frame = fetched.provider_replay_frame
    assert frame is not None
    assert frame["format"] == expected_format
    assert frame["complete"] is True
    if provider == "openai":
        call = frame["items"][-1]
        assert call["type"] == "function_call"
        assert json.loads(call["arguments"]) == {"x": 1}
    elif provider in {"anthropic", "hyperspace"}:
        call = frame["items"][-1]["content"][-1]
        assert call == {
            "type": "tool_use",
            "id": "call_1",
            "name": "demo",
            "input": {"x": 1},
        }
    else:
        call = frame["items"][-1]["tool_calls"][0]
        assert call["id"] == "call_1"
        assert call["function"]["arguments"] == {"x": 1}


def test_provider_model_turn_runtime_builds_request_and_applies_turn_state():
    from unchain.providers.model_turn_runtime import apply_model_turn_result, fetch_model_turn

    turn = ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[],
        final_text="done",
        response_id="resp-1",
        consumed_tokens=7,
        input_tokens=4,
        output_tokens=3,
        cache_read_input_tokens=2,
        cache_creation_input_tokens=1,
    )
    model_io = _RecordingModelIO(turn)
    state = RunState()
    state.seed_messages([{"role": "user", "content": "original"}])
    state.remote_continuation_input = [
        {"role": "user", "content": "resume-only"}
    ]
    state.provider_state.previous_response_id = "prev-1"
    state.provider_state.use_previous_response_chain = True
    state.token_state.consumed_tokens = 10
    state.token_state.input_tokens = 6
    state.token_state.output_tokens = 4
    state.token_state.cache_read_input_tokens = 3
    state.token_state.cache_creation_input_tokens = 2

    fetched = fetch_model_turn(
        model_io=model_io,
        retry_config=RetryConfig(max_retries=0),
        state=state,
        payload={"temperature": 0.2},
        toolkit=Toolkit(),
        run_id="run-1",
    )
    apply_model_turn_result(state, fetched)

    request = model_io.requests[0]
    assert request.messages == [{"role": "user", "content": "resume-only"}]
    assert request.payload == {"temperature": 0.2}
    assert request.previous_response_id == "prev-1"
    assert request.run_id == "run-1"

    assert state.provider_state.previous_response_id == "resp-1"
    assert state.next_model_input is None
    assert state.remote_continuation_input is None
    assert state.token_state.consumed_tokens == 17
    assert state.token_state.input_tokens == 10
    assert state.token_state.output_tokens == 7
    assert state.token_state.cache_read_input_tokens == 5
    assert state.token_state.cache_creation_input_tokens == 3
    assert state.token_state.last_turn_tokens == 7
    assert state.token_state.last_turn_input_tokens == 4
    assert state.token_state.last_turn_output_tokens == 3
    assert state.token_state.last_turn_cache_read_input_tokens == 2
    assert state.token_state.last_turn_cache_creation_input_tokens == 1


def test_fetch_built_model_turn_consumes_the_exact_prebuilt_request_once():
    from unchain.providers import model_turn_runtime

    assert hasattr(model_turn_runtime, "fetch_built_model_turn")

    turn = ModelTurnResult(
        assistant_messages=[{"role": "assistant", "content": "done"}],
        tool_calls=[],
        final_text="done",
    )
    model_io = _RecordingModelIO(turn)
    state = RunState()
    state.seed_messages([{"role": "user", "content": "original"}])
    state.provider_state.provider = "openai"
    request = model_turn_runtime.build_model_turn_request(
        state,
        payload={"temperature": 0.2},
        run_id="prebuilt-run",
    )
    attempts = []

    fetched = model_turn_runtime.fetch_built_model_turn(
        model_io=model_io,
        retry_config=RetryConfig(max_retries=0),
        state=state,
        request=request,
        before_attempt=attempts.append,
    )

    assert fetched.final_text == "done"
    assert model_io.requests == [request]
    assert model_io.requests[0] is request
    assert attempts == [0]
