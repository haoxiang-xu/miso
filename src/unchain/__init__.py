from __future__ import annotations

import importlib
import sys
from pathlib import Path

import miso as _miso
from miso import *  # noqa: F401,F403
from miso import __version__

__brand__ = "unchain"
__tagline__ = "unchain harness"

_ALIASED_PACKAGES = (
    "_internal",
    "agents",
    "characters",
    "input",
    "kernel",
    "memory",
    "runtime",
    "runtime.providers",
    "runtime.resources",
    "schemas",
    "toolkits",
    "toolkits.builtin",
    "tools",
    "workspace",
)

for _suffix in _ALIASED_PACKAGES:
    sys.modules.setdefault(f"{__name__}.{_suffix}", importlib.import_module(f"miso.{_suffix}"))

_here = Path(__file__).resolve().parent
_legacy_root = (_here.parent / "miso").resolve()
__path__ = [str(_here), str(_legacy_root)]
__all__ = [*getattr(_miso, "__all__", ()), "__brand__", "__tagline__"]
