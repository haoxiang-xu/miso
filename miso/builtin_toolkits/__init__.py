from __future__ import annotations

from pathlib import Path

from .base import builtin_toolkit
from .run_terminal_toolkit import run_terminal_toolkit, terminal_toolkit
from .external_api_toolkit import external_api_toolkit
from .ask_user_question_toolkit import ask_user_toolkit
from .access_workspace_toolkit import access_workspace_toolkit, workspace_toolkit

ask_user_question_toolkit = ask_user_toolkit


def build_builtin_toolkit(
    *,
    workspace_root: str | Path | None = None,
) -> access_workspace_toolkit:
    """Build an access_workspace_toolkit."""
    return access_workspace_toolkit(workspace_root=workspace_root)


__all__ = [
    "builtin_toolkit",
    "build_builtin_toolkit",
    "access_workspace_toolkit",
    "run_terminal_toolkit",
    "workspace_toolkit",
    "terminal_toolkit",
    "external_api_toolkit",
    "ask_user_toolkit",
    "ask_user_question_toolkit",
]
