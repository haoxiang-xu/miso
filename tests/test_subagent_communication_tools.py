from unchain.subagents import (
    build_close_agent_thread_tool,
    build_read_agent_board_tool,
    build_return_handoff_to_subagent_tool,
    build_return_to_parent_tool,
    build_send_agent_message_tool,
    build_spawn_agent_thread_tool,
    build_wait_agent_messages_tool,
    build_write_agent_board_tool,
)


def test_communication_runtime_tool_builders_have_expected_names():
    tools = [
        build_spawn_agent_thread_tool(),
        build_send_agent_message_tool(),
        build_wait_agent_messages_tool(),
        build_close_agent_thread_tool(),
        build_write_agent_board_tool(),
        build_read_agent_board_tool(),
        build_return_handoff_to_subagent_tool(),
        build_return_to_parent_tool(),
    ]

    assert [tool.name for tool in tools] == [
        "spawn_agent_thread",
        "send_agent_message",
        "wait_agent_messages",
        "close_agent_thread",
        "write_agent_board",
        "read_agent_board",
        "return_handoff_to_subagent",
        "return_to_parent",
    ]
