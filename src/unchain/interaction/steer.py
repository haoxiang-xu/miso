"""DEPRECATED shim — this module was renamed to ``unchain.interaction.queue_turns``.

CHANGELOG (0.2.0): the steer primitive was renamed to queued-turns —
``SteerBuffer`` -> ``QueuedTurnBuffer`` and ``merge_steered_texts`` ->
``merge_queued_turn_texts``. This shim re-exports the old names bound to the
new implementations so existing imports keep working unchanged, but emits a
``DeprecationWarning``. Removal is slated for the next minor release.
"""

from __future__ import annotations

import warnings

from .queue_turns import QueuedTurnBuffer as SteerBuffer
from .queue_turns import merge_queued_turn_texts as merge_steered_texts

# stacklevel note: this warning fires from the module body at import time, so
# there is no meaningful user frame to point at beyond the import machinery;
# stacklevel is left at the default (the shim itself) on purpose.
warnings.warn(
    "unchain.interaction.steer is deprecated and will be removed in the next "
    "minor release — import from unchain.interaction.queue_turns instead "
    "(SteerBuffer -> QueuedTurnBuffer, "
    "merge_steered_texts -> merge_queued_turn_texts).",
    DeprecationWarning,
)

__all__ = ["SteerBuffer", "merge_steered_texts"]
