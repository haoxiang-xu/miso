from __future__ import annotations

from ..tools import Tool, ToolParameter, tool


def build_delegate_to_subagent_tool() -> Tool:
    return tool(
        name="delegate_to_subagent",
        description="Delegate a focused subtask to a specialist subagent while keeping control of the current conversation.",
        func=lambda **_: {"error": "delegate_to_subagent is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="target", description="Template name or specialist role to delegate to.", type_="string", required=True),
            ToolParameter(name="task", description="The concrete delegated task.", type_="string", required=True),
            ToolParameter(name="instructions", description="Extra execution instructions for the subagent.", type_="string", required=False),
            ToolParameter(name="expected_output", description="Optional expected output contract.", type_="string", required=False),
            ToolParameter(name="output_mode", description="One of summary, last_message, or full_trace.", type_="string", required=False),
        ],
    )


def build_handoff_to_subagent_tool() -> Tool:
    return tool(
        name="handoff_to_subagent",
        description="Transfer the active conversation to a specialist subagent that should finish the task.",
        func=lambda **_: {"error": "handoff_to_subagent is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="target", description="Registered subagent template name.", type_="string", required=True),
            ToolParameter(name="reason", description="Why this handoff is appropriate.", type_="string", required=False),
            ToolParameter(name="carry_context", description="Whether to pass the current conversation context into the subagent.", type_="boolean", required=False),
        ],
    )


def build_spawn_worker_batch_tool() -> Tool:
    task_item = {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "target": {"type": "string"},
            "instructions": {"type": "string"},
            "expected_output": {"type": "string"},
            "output_mode": {"type": "string"},
        },
        "required": ["task"],
        "additionalProperties": False,
    }
    return tool(
        name="spawn_worker_batch",
        description="Run multiple worker subagents in parallel and return their results in input order.",
        func=lambda **_: {"error": "spawn_worker_batch is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="tasks", description="Array of worker task objects.", type_="array", required=True, items=task_item),
            ToolParameter(name="target", description="Optional default template or role for all workers.", type_="string", required=False),
            ToolParameter(name="instructions", description="Optional default extra instructions for all workers.", type_="string", required=False),
            ToolParameter(name="aggregate_mode", description="Aggregation mode label for the manager.", type_="string", required=False),
        ],
    )


def build_spawn_agent_thread_tool() -> Tool:
    return tool(
        name="spawn_agent_thread",
        description="Spawn an addressable subagent thread that can receive follow-up messages.",
        func=lambda **_: {"error": "spawn_agent_thread is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="target", description="Template name or specialist role.", type_="string", required=True),
            ToolParameter(name="task", description="Initial task for the thread.", type_="string", required=True),
            ToolParameter(name="instructions", description="Extra execution instructions.", type_="string", required=False),
            ToolParameter(name="expected_output", description="Expected output contract.", type_="string", required=False),
            ToolParameter(name="context_mode", description="One of none, summary, last_n, or full.", type_="string", required=False),
            ToolParameter(name="background", description="Whether to keep the thread addressable after returning.", type_="boolean", required=False),
            ToolParameter(name="return_mode", description="One of result, return_to_parent, or terminal_handoff.", type_="string", required=False),
        ],
    )


def build_send_agent_message_tool() -> Tool:
    return tool(
        name="send_agent_message",
        description="Send a directed message to an open agent thread.",
        func=lambda **_: {"error": "send_agent_message is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="recipient", description="Recipient agent id or thread id.", type_="string", required=True),
            ToolParameter(name="content", description="Message content.", type_="string", required=True),
            ToolParameter(name="kind", description="Message kind such as followup, task, status, or result.", type_="string", required=False),
            ToolParameter(name="thread_id", description="Optional thread id when recipient is an agent id.", type_="string", required=False),
            ToolParameter(name="correlation_id", description="Optional caller correlation id.", type_="string", required=False),
            ToolParameter(name="requires_ack", description="Whether the sender expects acknowledgement.", type_="boolean", required=False),
        ],
    )


def build_wait_agent_messages_tool() -> Tool:
    return tool(
        name="wait_agent_messages",
        description="Inspect or wait for one or more agent threads to reach a condition.",
        func=lambda **_: {"error": "wait_agent_messages is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="thread_ids", description="Thread ids to inspect.", type_="array", required=True, items={"type": "string"}),
            ToolParameter(name="condition", description="One of any_done, all_done, or idle.", type_="string", required=False),
            ToolParameter(name="timeout_seconds", description="Maximum wait time in seconds.", type_="number", required=False),
        ],
    )


def build_close_agent_thread_tool() -> Tool:
    return tool(
        name="close_agent_thread",
        description="Close an open agent thread.",
        func=lambda **_: {"error": "close_agent_thread is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="thread_id", description="Thread id to close.", type_="string", required=True),
            ToolParameter(name="reason", description="Reason for closing the thread.", type_="string", required=False),
        ],
    )


def build_write_agent_board_tool() -> Tool:
    return tool(
        name="write_agent_board",
        description="Write a structured item to the shared agent blackboard.",
        func=lambda **_: {"error": "write_agent_board is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="kind", description="Item kind such as finding, evidence, plan, risk, decision, question, or summary.", type_="string", required=True),
            ToolParameter(name="title", description="Short item title.", type_="string", required=True),
            ToolParameter(name="content", description="Item content.", type_="string", required=True),
            ToolParameter(name="board_id", description="Board id. Defaults to default.", type_="string", required=False),
            ToolParameter(name="tags", description="Optional tags.", type_="array", required=False, items={"type": "string"}),
            ToolParameter(name="confidence", description="Optional confidence from 0 to 1.", type_="number", required=False),
            ToolParameter(name="refs", description="Optional source references.", type_="array", required=False, items={"type": "string"}),
            ToolParameter(name="supersedes_item_id", description="Optional item id superseded by this item.", type_="string", required=False),
        ],
    )


def build_read_agent_board_tool() -> Tool:
    return tool(
        name="read_agent_board",
        description="Read filtered items from the shared agent blackboard.",
        func=lambda **_: {"error": "read_agent_board is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="board_id", description="Board id. Defaults to default.", type_="string", required=False),
            ToolParameter(name="kinds", description="Optional item kinds to include.", type_="array", required=False, items={"type": "string"}),
            ToolParameter(name="tags", description="Optional tags to match.", type_="array", required=False, items={"type": "string"}),
            ToolParameter(name="author_agent_id", description="Optional author filter.", type_="string", required=False),
            ToolParameter(name="limit", description="Maximum number of items.", type_="integer", required=False),
        ],
    )


def build_return_handoff_to_subagent_tool() -> Tool:
    return tool(
        name="return_handoff_to_subagent",
        description="Temporarily hand control to a subagent and return the result to the parent.",
        func=lambda **_: {"error": "return_handoff_to_subagent is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="target", description="Registered subagent template name.", type_="string", required=True),
            ToolParameter(name="reason", description="Why temporary handoff is appropriate.", type_="string", required=False),
            ToolParameter(name="carry_context", description="Whether to pass current conversation context.", type_="boolean", required=False),
            ToolParameter(name="expected_return", description="Expected return contract.", type_="string", required=False),
        ],
    )


def build_return_to_parent_tool() -> Tool:
    return tool(
        name="return_to_parent",
        description="Return a temporary handoff result to the parent agent.",
        func=lambda **_: {"error": "return_to_parent is a reserved runtime tool and cannot be executed directly"},
        parameters=[
            ToolParameter(name="summary", description="Concise return summary.", type_="string", required=True),
            ToolParameter(name="result", description="Detailed result.", type_="string", required=False),
            ToolParameter(name="status", description="completed, blocked, or failed.", type_="string", required=False),
        ],
    )
