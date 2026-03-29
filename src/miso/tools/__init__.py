from __future__ import annotations

import importlib
import sys

__all__ = list(importlib.import_module("unchain.tools").__all__)

_ALIASED_SUBMODULES = (
    "catalog",
    "confirmation",
    "decorators",
    "execution",
    "human_input",
    "messages",
    "models",
    "observation",
    "registry",
    "runtime",
    "tool",
    "toolkit",
    "types",
)

for _suffix in _ALIASED_SUBMODULES:
    sys.modules.setdefault(f"{__name__}.{_suffix}", importlib.import_module(f"unchain.tools.{_suffix}"))

tool = getattr(importlib.import_module("unchain.tools"), "tool")


def __getattr__(name: str):
    if name == "tool":
        return tool
    return getattr(importlib.import_module("unchain.tools"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
