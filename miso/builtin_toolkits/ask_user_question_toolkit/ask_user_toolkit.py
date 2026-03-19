from __future__ import annotations

from ...human_input import build_request_user_input_tool
from ...tool import toolkit


class ask_user_toolkit(toolkit):
    """Toolkit that exposes structured user-question tools."""

    def __init__(self):
        super().__init__()
        reserved_tool = build_request_user_input_tool()
        self.register(
            self.request_user_input,
            name=reserved_tool.name,
            description=reserved_tool.description,
            parameters=reserved_tool.parameters,
        )

    def request_user_input(self, **kwargs):
        """Reserved runtime placeholder for structured user input requests."""
        del kwargs
        return {
            "error": (
                "request_user_input is a reserved runtime tool and cannot be "
                "executed directly"
            ),
        }


ask_user_question_toolkit = ask_user_toolkit

__all__ = ["ask_user_toolkit", "ask_user_question_toolkit"]
