"""Terminal rendering for Unchain runs.

Use ``TerminalRenderer`` as a drop-in ``callback`` for ``Agent.run``:

    from unchain import Agent
    from unchain.render import TerminalRenderer

    renderer = TerminalRenderer()
    Agent(...).run("hello", callback=renderer)
    renderer.print_summary()

This subpackage requires ``rich``. Install it via the ``render`` extra:

    pip install unchain[render]
"""

from __future__ import annotations

from .terminal import RenderTotals, TerminalRenderer

__all__ = ["TerminalRenderer", "RenderTotals"]
