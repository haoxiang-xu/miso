from __future__ import annotations

import importlib
import sys

__all__ = list(importlib.import_module("unchain.memory").__all__)

_ALIASED_SUBMODULES = (
    "base",
    "bootstrap",
    "commit",
    "config",
    "long_term",
    "manager",
    "qdrant",
    "recall_long_term",
    "runtime",
    "short_term",
    "stores",
    "strategies",
    "tool_history",
)

for _suffix in _ALIASED_SUBMODULES:
    sys.modules.setdefault(f"{__name__}.{_suffix}", importlib.import_module(f"unchain.memory.{_suffix}"))


def __getattr__(name: str):
    return getattr(importlib.import_module("unchain.memory"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
