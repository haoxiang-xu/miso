from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from unchain.context.semantic_refs import (
    SemanticRefContract,
    SemanticRefGroup,
    semantic_ref_contract,
    semantic_ref_contracts,
)


def _groups(contract: SemanticRefContract) -> dict[str, SemanticRefGroup]:
    return {group.name: group for group in contract.groups}


def test_contracts_enumerate_the_exact_supported_semantic_event_types() -> None:
    assert {
        contract.name: contract.event_types for contract in semantic_ref_contracts()
    } == {
        "message": ("message.user", "message.assistant"),
        "tool_call": ("tool_call",),
        "tool_result": ("tool_result", "tool.result"),
        "artifact": (
            "artifact_created",
            "artifact_updated",
            "artifact.created",
            "artifact.updated",
            "artifact.recorded",
        ),
        "handoff": (
            "subagent_completed",
            "subagent_failed",
            "subagent_cancelled",
            "subagent_canceled",
            "agent_thread_completed",
            "agent_thread_failed",
            "subagent_return_handoff_completed",
            "handoff.recorded",
        ),
        "interaction": (
            "interaction_requested",
            "tool_confirmation_requested",
            "human_input_requested",
        ),
    }


@pytest.mark.parametrize("event_type", ("tool_result", "tool.result"))
def test_tool_result_has_one_required_artifact_ref_slot(event_type: str) -> None:
    contract = semantic_ref_contract(event_type)

    assert contract is not None
    assert contract.groups == (
        SemanticRefGroup(
            name="full_output",
            paths=(("full_output_ref",),),
            allowed_kinds=("artifact",),
            required=True,
        ),
    )
    assert contract.is_free_text_path(("result", "artifact_ref"))
    assert contract.ref_group_for_path(("result", "artifact_ref")) is None


@pytest.mark.parametrize(
    "event_type",
    (
        "artifact_created",
        "artifact_updated",
        "artifact.created",
        "artifact.updated",
        "artifact.recorded",
    ),
)
def test_artifact_events_share_one_required_alias_group(event_type: str) -> None:
    contract = semantic_ref_contract(event_type)

    assert contract is not None
    assert contract.groups == (
        SemanticRefGroup(
            name="artifact",
            paths=(
                ("artifact_ref",),
                ("artifact", "artifact_ref"),
                ("artifact", "content_ref"),
                ("artifact", "ref"),
            ),
            allowed_kinds=("artifact",),
            required=True,
        ),
    )
    assert contract.is_free_text_path(("artifact", "description", "content_ref"))
    assert contract.ref_group_for_path(
        ("artifact", "description", "content_ref")
    ) is None


@pytest.mark.parametrize(
    "event_type",
    (
        "subagent_completed",
        "subagent_failed",
        "subagent_cancelled",
        "subagent_canceled",
        "agent_thread_completed",
        "agent_thread_failed",
        "subagent_return_handoff_completed",
        "handoff.recorded",
    ),
)
def test_handoff_events_have_exact_output_and_artifact_groups(
    event_type: str,
) -> None:
    contract = semantic_ref_contract(event_type)

    assert contract is not None
    assert _groups(contract) == {
        "full_output": SemanticRefGroup(
            name="full_output",
            paths=(
                ("handoff_envelope", "full_output_ref"),
                ("full_output_ref",),
                ("handoff_envelope", "handoff_ref"),
                ("handoff_ref",),
                ("handoff_envelope", "artifact_ref"),
                ("artifact_ref",),
                ("handoff_envelope", "content_ref"),
                ("content_ref",),
            ),
            allowed_kinds=("artifact",),
            required=True,
        ),
        "artifacts": SemanticRefGroup(
            name="artifacts",
            paths=(
                ("handoff_envelope", "artifact_refs"),
                ("artifact_refs",),
            ),
            allowed_kinds=("artifact",),
            required=False,
            repeated=True,
        ),
    }
    assert contract.is_free_text_path(("handoff_envelope", "summary", "memory_ref"))
    assert contract.ref_group_for_path(
        ("handoff_envelope", "summary", "memory_ref")
    ) is None


@pytest.mark.parametrize(
    "event_type",
    (
        "interaction_requested",
        "tool_confirmation_requested",
        "human_input_requested",
    ),
)
def test_interaction_content_ref_is_optional_and_kind_bounded(
    event_type: str,
) -> None:
    contract = semantic_ref_contract(event_type)

    assert contract is not None
    assert contract.groups == (
        SemanticRefGroup(
            name="content",
            paths=(("content_ref",),),
            allowed_kinds=("artifact", "checkpoint", "context_event", "memory"),
            required=False,
        ),
    )
    assert contract.is_free_text_path(
        ("interaction_request", "payload", "content_ref")
    )
    assert contract.ref_group_for_path(
        ("interaction_request", "payload", "content_ref")
    ) is None


def test_message_and_tool_call_bodies_are_free_text_not_ref_slots() -> None:
    message = semantic_ref_contract("message.user")
    tool_call = semantic_ref_contract("tool_call")

    assert message is not None
    assert len(message.groups) == 1
    assert message.groups[0].name == "attachments"
    assert message.groups[0].paths == (("attachment_refs",),)
    assert message.groups[0].allowed_kinds == ("artifact",)
    assert message.groups[0].required is False
    assert message.groups[0].repeated is True
    assert message.free_text_roots == (("message", "content"),)
    assert message.is_free_text_path(("message", "content", "artifact_ref"))
    assert message.ref_group_for_path(
        ("message", "content", "artifact_ref")
    ) is None
    assert not message.is_free_text_path(("content", "artifact_ref"))

    assert tool_call is not None
    assert tool_call.groups == ()
    assert tool_call.is_free_text_path(("arguments", "memory_ref"))
    assert tool_call.ref_group_for_path(("arguments", "memory_ref")) is None


def test_unknown_events_and_ref_like_names_have_no_implicit_semantics() -> None:
    assert semantic_ref_contract("custom.event") is None
    assert semantic_ref_contract("pupu://artifact/not-an-event@1") is None

    artifact = semantic_ref_contract("artifact.recorded")
    assert artifact is not None
    assert artifact.ref_group_for_path(("arbitrary", "artifact_ref")) is None


def test_contract_records_and_collections_are_immutable() -> None:
    contract = semantic_ref_contract("tool_result")

    assert contract is not None
    with pytest.raises(FrozenInstanceError):
        contract.name = "changed"
    with pytest.raises(FrozenInstanceError):
        contract.groups[0].required = False
    with pytest.raises(TypeError):
        contract.groups[0].paths[0][0] = "changed"
