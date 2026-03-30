from __future__ import annotations

from typing import Any

from ..tools.tool import Tool


def _update_traits(updates: dict[str, Any]) -> dict[str, Any]:
    """Update one or more character traits.

    Args:
        updates: A dict of trait names to new values.
                 Example: {"mood": "happy", "trust": 0.8}

    Returns:
        Confirmation of applied updates.
    """
    return {
        "applied": True,
        "updates": updates,
    }


def _update_schedule(action: str, block: dict[str, Any] | None = None, block_id: str | None = None) -> dict[str, Any]:
    """Modify the character's schedule.

    Args:
        action: One of "add", "remove", "update".
        block: Schedule block data (for "add" or "update"). Must include "id", "label", "time_range", "behavior".
        block_id: ID of the block to remove or update (for "remove" or "update").

    Returns:
        Confirmation of the schedule change.
    """
    return {
        "applied": True,
        "action": action,
        "block": block,
        "block_id": block_id,
    }


def _write_journal(entry: str) -> dict[str, Any]:
    """Record an internal thought, observation, or reflection.

    The character uses this to note important realizations, emotional shifts,
    or observations about the user that may influence future behavior.

    Args:
        entry: The journal entry text.

    Returns:
        Confirmation that the entry was recorded.
    """
    return {
        "recorded": True,
        "entry": entry,
    }


def build_character_tools() -> list[Tool]:
    """Build the set of tools available to a character agent."""
    return [
        Tool.from_callable(
            _update_traits,
            name="update_character_traits",
            description=(
                "Update your own dynamic traits such as mood, trust level, "
                "emotional state, or knowledge. Call this when your internal "
                "state changes as a result of the conversation."
            ),
        ),
        Tool.from_callable(
            _update_schedule,
            name="update_character_schedule",
            description=(
                "Modify your own daily schedule. You can add new activities, "
                "remove existing ones, or update time ranges and behaviors."
            ),
        ),
        Tool.from_callable(
            _write_journal,
            name="character_journal",
            description=(
                "Record an internal thought or observation in your private journal. "
                "Use this to note important realizations, relationship dynamics, "
                "or plans for future interactions."
            ),
        ),
    ]
