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
