from .base import AgentModule, BaseAgentModule
from .memory import MemoryModule
from .optimizers import OptimizersModule
from .policies import PoliciesModule
from .subagents import SubagentModule
from .tools import ToolsModule

__all__ = [
    "AgentModule",
    "BaseAgentModule",
    "CharacterModule",
    "MemoryModule",
    "OptimizersModule",
    "PoliciesModule",
    "SubagentModule",
    "ToolsModule",
]


def __getattr__(name):
    if name == "CharacterModule":
        from ...character.module import CharacterModule
        return CharacterModule
    raise AttributeError(name)
