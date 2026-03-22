from __future__ import annotations

from ....input.human_input import build_ask_user_question_tool
from ....tools.toolkit import Toolkit


class AskUserToolkit(Toolkit):
    """Toolkit that exposes structured user-question tools."""

    def __init__(self):
        super().__init__()
        reserved_tool = build_ask_user_question_tool()
        self.register(
            self.ask_user_question,
            name=reserved_tool.name,
            description=reserved_tool.description,
            parameters=reserved_tool.parameters,
        )

    def ask_user_question(self, **kwargs):
        """Reserved runtime placeholder for structured user input requests."""
        del kwargs
        return {
            "error": (
                "ask_user_question is a reserved runtime tool and cannot be "
                "executed directly"
            ),
        }


__all__ = ["AskUserToolkit"]
