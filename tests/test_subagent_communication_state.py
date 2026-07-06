import pytest

from unchain.subagents.communication import (
    AgentCommunicationRuntime,
    AgentMessage,
    AgentThreadRecord,
    BlackboardItem,
)
from unchain.subagents.types import SubagentPolicy, SubagentState


def test_subagent_state_preserves_communication_fields_across_copy_and_raw_roundtrip():
    state = SubagentState(root_agent_id="root", active_agent_id="root")
    state.threads["thread-1"] = {
        "thread_id": "thread-1",
        "agent_id": "root.worker.1",
        "parent_agent_id": "root",
        "target": "worker",
        "status": "idle",
        "lineage": ["root", "root.worker.1"],
    }
    state.mailboxes["root.worker.1"] = [
        {
            "message_id": "msg-1",
            "sender_agent_id": "root",
            "recipient_agent_id": "root.worker.1",
            "thread_id": "thread-1",
            "kind": "task",
            "content": "Check the bug",
        }
    ]
    state.blackboards["default"] = [
        {
            "item_id": "item-1",
            "board_id": "default",
            "author_agent_id": "root.worker.1",
            "kind": "finding",
            "title": "Bug source",
            "content": "The parser drops empty chunks.",
            "tags": ["parser"],
        }
    ]
    state.return_handoff_stack.append(
        {
            "frame_id": "frame-1",
            "parent_agent_id": "root",
            "child_agent_id": "root.specialist.1",
            "thread_id": "thread-2",
        }
    )

    copied = state.copy()
    copied.threads["thread-1"]["status"] = "closed"
    copied.mailboxes["root.worker.1"][0]["content"] = "changed"
    copied.blackboards["default"][0]["title"] = "changed"
    copied.return_handoff_stack[0]["frame_id"] = "changed"

    assert state.threads["thread-1"]["status"] == "idle"
    assert state.mailboxes["root.worker.1"][0]["content"] == "Check the bug"
    assert state.blackboards["default"][0]["title"] == "Bug source"
    assert state.return_handoff_stack[0]["frame_id"] == "frame-1"

    roundtripped = SubagentState.from_raw(state.to_dict())

    assert roundtripped.threads == state.threads
    assert roundtripped.mailboxes == state.mailboxes
    assert roundtripped.blackboards == state.blackboards
    assert roundtripped.return_handoff_stack == state.return_handoff_stack


def test_subagent_state_merges_communication_fields_without_dropping_existing_state():
    state = SubagentState(root_agent_id="root", active_agent_id="root")
    state.threads["existing"] = {"thread_id": "existing", "status": "idle"}
    state.mailboxes["root"] = [{"message_id": "old", "content": "old"}]

    merged = state.merged(
        {
            "threads": {"new": {"thread_id": "new", "status": "running"}},
            "mailboxes": {"child": [{"message_id": "new-msg", "content": "new"}]},
            "blackboards": {"default": [{"item_id": "item", "title": "Finding"}]},
            "return_handoff_stack": [{"frame_id": "frame"}],
        }
    )

    assert set(merged.threads) == {"existing", "new"}
    assert merged.mailboxes["root"][0]["message_id"] == "old"
    assert merged.mailboxes["child"][0]["message_id"] == "new-msg"
    assert merged.blackboards["default"][0]["item_id"] == "item"
    assert merged.return_handoff_stack[0]["frame_id"] == "frame"


def test_subagent_policy_has_conservative_communication_defaults():
    policy = SubagentPolicy()

    assert policy.max_open_threads == 10
    assert policy.max_mailbox_messages == 100
    assert policy.max_message_chars == 8000
    assert policy.max_board_items == 200
    assert policy.max_board_item_chars == 12000
    assert policy.allow_child_to_child_messages is False
    assert policy.allow_broadcast_messages is False
    assert policy.allow_return_handoff is True
    assert policy.retain_completed_threads is True


def test_agent_thread_record_roundtrips_as_dict():
    record = AgentThreadRecord(
        thread_id="thread-1",
        agent_id="root.worker.1",
        parent_agent_id="root",
        target="worker",
        template_name="worker",
        mode="thread",
        status="idle",
        session_id="session:thread-1",
        memory_namespace="memory:thread-1",
        lineage=("root", "root.worker.1"),
        created_iteration=1,
        last_activity_iteration=2,
        context_mode="none",
        instructions="Stay focused.",
        expected_output="Summary",
    )

    raw = record.to_dict()
    parsed = AgentThreadRecord.from_raw(raw)

    assert parsed == record
    assert raw["lineage"] == ["root", "root.worker.1"]


def test_agent_message_rejects_oversized_content():
    runtime = AgentCommunicationRuntime(SubagentPolicy(max_message_chars=5))

    with pytest.raises(ValueError, match="message exceeds max_message_chars"):
        runtime.build_message(
            sender_agent_id="root",
            recipient_agent_id="root.worker.1",
            thread_id="thread-1",
            kind="followup",
            content="too long",
            iteration=1,
        )


def test_blackboard_item_rejects_oversized_content():
    runtime = AgentCommunicationRuntime(SubagentPolicy(max_board_item_chars=5))

    with pytest.raises(ValueError, match="board item exceeds max_board_item_chars"):
        runtime.build_board_item(
            board_id="default",
            author_agent_id="root.worker.1",
            kind="finding",
            title="Finding",
            content="too long",
            tags=("parser",),
            confidence=0.9,
            refs=("src/file.py:10",),
            iteration=1,
        )


def test_runtime_opens_thread_sends_message_and_closes_thread():
    runtime = AgentCommunicationRuntime(SubagentPolicy(max_open_threads=1))
    state = SubagentState(root_agent_id="root", active_agent_id="root")
    record = AgentThreadRecord(
        thread_id="thread-1",
        agent_id="root.worker.1",
        parent_agent_id="root",
        target="worker",
        template_name="worker",
        mode="thread",
        status="idle",
        session_id="session:thread-1",
        memory_namespace="memory:thread-1",
        lineage=("root", "root.worker.1"),
        created_iteration=1,
        last_activity_iteration=1,
        context_mode="none",
    )

    state = runtime.upsert_thread(state, record)
    message = runtime.build_message(
        sender_agent_id="root",
        recipient_agent_id="root.worker.1",
        thread_id="thread-1",
        kind="task",
        content="Inspect parser",
        iteration=1,
    )
    state = runtime.append_message(state, message)
    state = runtime.close_thread(state, "thread-1", reason="done")

    assert state.threads["thread-1"]["status"] == "closed"
    assert state.threads["thread-1"]["close_reason"] == "done"
    assert state.mailboxes["root.worker.1"][0]["content"] == "Inspect parser"
