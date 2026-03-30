from __future__ import annotations

import importlib
from pathlib import Path

__brand__ = "unchain"
__tagline__ = "unchain harness"
__version__ = "0.2.0"

_here = Path(__file__).resolve().parent
__path__ = [str(_here)]

_LAZY_EXPORTS = {
    "Agent",
    "Team",
    "CharacterAgent",
    "CharacterDecision",
    "CharacterEvaluation",
    "CharacterSchedule",
    "CharacterScheduleBlock",
    "CharacterSpec",
}

__all__ = sorted([*_LAZY_EXPORTS, "__brand__", "__tagline__", "__version__"])


def __getattr__(name: str):
    if name in _LAZY_EXPORTS:
        miso = importlib.import_module("miso")
        return getattr(miso, name)
    raise AttributeError(name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | _LAZY_EXPORTS)
