from __future__ import annotations

import importlib
import sys

__all__ = list(importlib.import_module("unchain.toolkits").__all__)

_ALIASED_SUBMODULES = (
    "base",
    "builtin",
    "builtin.ask_user",
    "builtin.external_api",
    "builtin.terminal",
    "builtin.terminal_runtime",
    "builtin.workspace",
    "mcp",
)

for _suffix in _ALIASED_SUBMODULES:
    sys.modules.setdefault(f"{__name__}.{_suffix}", importlib.import_module(f"unchain.toolkits.{_suffix}"))


def __getattr__(name: str):
    return getattr(importlib.import_module("unchain.toolkits"), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
