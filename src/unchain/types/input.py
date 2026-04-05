from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class InputRequest:
    """Unified request for user input during agent execution.

    kind:
        "approval"  — tool execution approval (config: arguments, description)
        "question"  — ask_user_question (config: title, question, options, ...)
        "continue"  — max iterations reached (config: reason; call_id is None)
    """

    kind: str
    run_id: str
    call_id: str | None
    tool_name: str | None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InputResponse:
    """User's response to an InputRequest.

    decision:
        "approved" | "denied"     — for kind=approval
        "submitted"               — for kind=question
        "continued" | "stopped"   — for kind=continue
    """

    decision: str
    response: dict[str, Any] | None = None
