from __future__ import annotations

from pathlib import Path

from ....input.human_input import build_ask_user_question_tool
from ...base import BuiltinToolkit


class InteractionToolkit(BuiltinToolkit):
    """Focused human interaction toolkit."""

    def __init__(
        self,
        *,
        workspace_root: str | Path | None = None,
        workspace_roots: list[str | Path] | None = None,
    ) -> None:
        super().__init__(workspace_root=workspace_root, workspace_roots=workspace_roots)
        self.register(build_ask_user_question_tool())


__all__ = ["InteractionToolkit"]
