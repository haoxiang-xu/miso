from __future__ import annotations

import re
import uuid


def sanitize_character_key_component(value: object, fallback: str = "default") -> str:
    raw = str(value or "").strip().lower()
    sanitized = re.sub(r"[^a-z0-9]+", "_", raw).strip("_")
    return sanitized or fallback


def generate_character_id(name: object = "") -> str:
    base = sanitize_character_key_component(name, fallback="character")
    return f"{base}_{uuid.uuid4().hex[:8]}"


def make_character_self_namespace(character_id: object) -> str:
    safe_id = sanitize_character_key_component(character_id, fallback="character")
    return f"character_{safe_id}__self"


def make_character_relationship_namespace(
    character_id: object,
    human_id: object = "local_user",
) -> str:
    safe_id = sanitize_character_key_component(character_id, fallback="character")
    safe_human_id = sanitize_character_key_component(human_id, fallback="local_user")
    return f"character_{safe_id}__rel__{safe_human_id}"


def make_character_direct_session_id(
    character_id: object,
    thread_id: object,
) -> str:
    safe_id = sanitize_character_key_component(character_id, fallback="character")
    safe_thread_id = sanitize_character_key_component(thread_id, fallback="default")
    return f"character_{safe_id}__dm__{safe_thread_id}"
