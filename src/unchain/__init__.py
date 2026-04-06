from __future__ import annotations

from pathlib import Path

__brand__ = "unchain"
__tagline__ = "unchain harness"
__version__ = "0.2.0"

_here = Path(__file__).resolve().parent
__path__ = [str(_here)]

__all__ = [
    "Agent",
    "__brand__",
    "__tagline__",
    "__version__",
]


def __getattr__(name: str):
    if name == "Agent":
        from .agent.agent import Agent
        return Agent
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | {"Agent"})
