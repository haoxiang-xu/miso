from __future__ import annotations

import importlib

__all__ = [
    "SubagentExecutor",
    "SubagentMemoryPolicy",
    "SubagentMode",
    "SubagentOutputMode",
    "SubagentPolicy",
    "SubagentResult",
    "SubagentState",
    "SubagentTemplate",
    "build_delegate_to_subagent_tool",
    "build_handoff_to_subagent_tool",
    "build_spawn_worker_batch_tool",
]

_TYPE_EXPORTS = {
    "SubagentMemoryPolicy",
    "SubagentMode",
    "SubagentOutputMode",
    "SubagentPolicy",
    "SubagentResult",
    "SubagentState",
    "SubagentTemplate",
}
_EXECUTOR_EXPORTS = {"SubagentExecutor"}
_RUNTIME_TOOL_EXPORTS = {
    "build_delegate_to_subagent_tool",
    "build_handoff_to_subagent_tool",
    "build_spawn_worker_batch_tool",
}


def __getattr__(name: str):
    if name in _TYPE_EXPORTS:
        module = importlib.import_module(".types", __name__)
        return getattr(module, name)
    if name in _EXECUTOR_EXPORTS:
        module = importlib.import_module(".executor", __name__)
        return getattr(module, name)
    if name in _RUNTIME_TOOL_EXPORTS:
        module = importlib.import_module(".runtime_tools", __name__)
        return getattr(module, name)
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
