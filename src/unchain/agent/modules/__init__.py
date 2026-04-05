from .base import AgentModule, BaseAgentModule
from .memory import MemoryModule
from .optimizers import OptimizersModule
from .policies import PoliciesModule
from .subagents import SubagentModule
from .tool_discovery import ToolDiscoveryModule
from .tools import ToolsModule

__all__ = [
    "AgentModule",
    "BaseAgentModule",
    "CharacterModule",
    "MemoryModule",
    "OptimizersModule",
    "PoliciesModule",
    "SubagentModule",
    "ToolDiscoveryModule",
    "ToolsModule",
]


def __getattr__(name):
    if name == "CharacterModule":
        from ...character.module import CharacterModule
        return CharacterModule
    raise AttributeError(name)
