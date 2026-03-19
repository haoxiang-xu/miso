from __future__ import annotations

import copy
import inspect
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


SummaryGenerator = Callable[[str, list[dict[str, Any]], int, str], str]
LongTermExtractor = Callable[..., dict[str, Any]]


@runtime_checkable
class SessionStore(Protocol):
    def load(self, session_id: str) -> dict[str, Any]:
        ...

    def save(self, session_id: str, state: dict[str, Any]) -> None:
        ...


@runtime_checkable
class VectorStoreAdapter(Protocol):
    def add_texts(
        self,
        *,
        session_id: str,
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        ...

    def similarity_search(
        self,
        *,
        session_id: str,
        query: str,
        k: int,
        min_score: float | None = None,
    ) -> list[str | dict[str, Any]]:
        ...


@runtime_checkable
class LongTermProfileStore(Protocol):
    def load(self, namespace: str) -> dict[str, Any]:
        ...

    def save(self, namespace: str, profile: dict[str, Any]) -> None:
        ...


@runtime_checkable
class LongTermVectorAdapter(Protocol):
    def add_texts(
        self,
        *,
        namespace: str,
        texts: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        ...

    def similarity_search(
        self,
        *,
        namespace: str,
        query: str,
        k: int,
        filters: dict[str, Any] | None = None,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class ContextStrategy(Protocol):
    def prepare(
        self,
        *,
        state: dict[str, Any],
        incoming: list[dict[str, Any]],
        max_context_window_tokens: int,
        model: str,
    ) -> list[dict[str, Any]]:
        ...

    def commit(
        self,
        *,
        state: dict[str, Any],
        full_conversation: list[dict[str, Any]],
    ) -> None:
        ...


class InMemorySessionStore:
    """Process-local session state store."""

    def __init__(self):
        self._sessions: dict[str, dict[str, Any]] = {}

    def load(self, session_id: str) -> dict[str, Any]:
        return copy.deepcopy(self._sessions.get(session_id, {}))

    def save(self, session_id: str, state: dict[str, Any]) -> None:
        self._sessions[session_id] = copy.deepcopy(state)


class JsonFileLongTermProfileStore:
    """Profile store backed by one JSON file per namespace."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base = Path(base_dir) if base_dir is not None else (_default_user_data_dir() / "long_term_profiles")
        self._base.mkdir(parents=True, exist_ok=True)

    def _path(self, namespace: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in namespace).strip().replace(" ", "_")
        return self._base / f"{safe or 'default'}.json"

    def load(self, namespace: str) -> dict[str, Any]:
        p = self._path(namespace)
        if not p.exists():
            return {}
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return copy.deepcopy(raw) if isinstance(raw, dict) else {}

    def save(self, namespace: str, profile: dict[str, Any]) -> None:
        p = self._path(namespace)
        p.write_text(json.dumps(profile, default=str, ensure_ascii=False), encoding="utf-8")


@dataclass
class LongTermMemoryConfig:
    profile_store: LongTermProfileStore | None = None
    vector_adapter: LongTermVectorAdapter | None = None
    extractor: LongTermExtractor | None = None
    vector_top_k: int = 4
    vector_min_score: float | None = None
    episode_top_k: int = 2
    episode_min_score: float | None = None
    playbook_top_k: int = 2
    playbook_min_score: float | None = None
    max_profile_chars: int = 1200
    max_fact_items: int = 6
    max_episode_items: int = 3
    max_playbook_items: int = 2
    extract_every_n_turns: int = 1
    embedding_model: str = "text-embedding-3-small"
    embedding_payload: dict[str, Any] | None = None
    profile_base_dir: str | Path | None = None
    qdrant_path: str | Path | None = None
    collection_prefix: str = "long_term"


@dataclass
class MemoryConfig:
    last_n_turns: int = 8
    summary_trigger_pct: float = 0.75
    summary_target_pct: float = 0.45
    max_summary_chars: int = 2400
    vector_top_k: int = 4
    vector_min_score: float | None = None
    vector_adapter: VectorStoreAdapter | None = None
    long_term: LongTermMemoryConfig | None = None


def _default_user_data_dir() -> Path:
    try:
        from platformdirs import user_data_dir

        return Path(user_data_dir("miso", "miso"))
    except Exception:
        return Path.home() / ".miso"


def _deepcopy_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [copy.deepcopy(m) for m in messages if isinstance(m, dict)]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type in ("text", "input_text", "output_text"):
                text = block.get("text", "")
                if text:
                    parts.append(text if isinstance(text, str) else str(text))
                continue
            if block_type in ("image", "input_image"):
                parts.append("[image]")
                continue
            if block_type in ("pdf", "document", "input_file"):
                parts.append("[pdf]")
                continue
            parts.append(json.dumps(block, default=str, ensure_ascii=False))
        return "".join(parts)
    if isinstance(content, dict):
        return json.dumps(content, default=str, ensure_ascii=False)
    if content is None:
        return ""
    return str(content)


def _message_to_text(message: dict[str, Any]) -> str:
    if not isinstance(message, dict):
        return ""
    if "content" in message:
        return _content_to_text(message.get("content"))
    text_parts: list[str] = []
    for key in ("arguments", "output", "result", "text"):
        if key not in message:
            continue
        value = message.get(key)
        if isinstance(value, str):
            text_parts.append(value)
        else:
            text_parts.append(json.dumps(value, default=str, ensure_ascii=False))
    if text_parts:
        return " ".join(text_parts)
    return json.dumps(message, default=str, ensure_ascii=False)


def _normalize_dialog_role(role: Any) -> str | None:
    if not isinstance(role, str):
        return None
    normalized = role.strip().lower()
    if normalized in ("user", "assistant"):
        return normalized
    return None


def _normalize_recall_messages_array(raw_messages: Any) -> list[dict[str, str]]:
    if not isinstance(raw_messages, list):
        return []
    normalized: list[dict[str, str]] = []
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        role = _normalize_dialog_role(item.get("role"))
        if role is None:
            continue
        raw_content = item.get("content")
        if raw_content is None:
            raw_content = item.get("text")
        content = _content_to_text(raw_content).strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def _parse_prefixed_dialog_text(text: str) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    current_role: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        nonlocal current_role, current_lines
        if current_role is None:
            current_lines = []
            return
        content = "\n".join(line for line in current_lines if isinstance(line, str)).strip()
        if content:
            parsed.append({"role": current_role, "content": content})
        current_role = None
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith("user:") or lowered.startswith("assistant:"):
            _flush()
            role = "user" if lowered.startswith("user:") else "assistant"
            content = line.split(":", 1)[1].strip() if ":" in line else ""
            current_role = role
            current_lines = [content] if content else []
            continue

        if current_role is not None:
            current_lines.append(raw_line)

    _flush()
    return parsed


def _normalize_recall_legacy_item(item: str | dict[str, Any]) -> list[dict[str, str]]:
    role: str | None = None
    raw_text: Any = ""
    if isinstance(item, str):
        raw_text = item
    elif isinstance(item, dict):
        raw_text = item.get("text")
        if raw_text is None:
            raw_text = item.get("content")
        role = _normalize_dialog_role(item.get("role"))
    else:
        return []

    text = _content_to_text(raw_text).strip()
    if not text:
        return []

    parsed = _parse_prefixed_dialog_text(text)
    if parsed:
        return parsed
    if role is not None:
        return [{"role": role, "content": text}]
    return [{"content": text}]


def _normalize_recalled_messages(
    recalled: list[str | dict[str, Any]],
) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for item in recalled:
        if isinstance(item, dict):
            parsed_messages = _normalize_recall_messages_array(item.get("messages"))
            if parsed_messages:
                normalized.extend(parsed_messages)
                continue
        normalized.extend(_normalize_recall_legacy_item(item))
    return normalized


def _infer_recall_roles_from_messages(
    recalled: list[dict[str, str]],
    messages: list[dict[str, Any]],
) -> list[dict[str, str]]:
    text_to_role: dict[str, str] = {}
    text_role_pairs: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        normalized_role = _normalize_dialog_role(role)
        if normalized_role is None:
            continue
        text = _message_to_text(message).strip()
        if not text:
            continue
        text_to_role[text] = normalized_role
        text_role_pairs.append((text, normalized_role))

    output: list[dict[str, str]] = []
    for item in recalled:
        normalized = dict(item)
        if "role" not in normalized:
            item_text = normalized.get("content", "")
            if not item_text:
                item_text = normalized.get("text", "")
            matched_role = text_to_role.get(item_text)
            if not matched_role and item_text:
                candidate_roles = {
                    role
                    for message_text, role in text_role_pairs
                    if item_text in message_text or message_text in item_text
                }
                if len(candidate_roles) == 1:
                    matched_role = next(iter(candidate_roles))
            if matched_role:
                normalized["role"] = matched_role
        output.append(normalized)
    return output


def _coerce_recall_messages(recalled: list[dict[str, str]]) -> list[dict[str, str]]:
    coerced: list[dict[str, str]] = []
    for item in recalled:
        content = _content_to_text(item.get("content")).strip()
        if not content:
            continue
        role = _normalize_dialog_role(item.get("role"))
        if role is None:
            role = "assistant"
        coerced.append({"role": role, "content": content})
    return coerced


def _dedupe_adjacent_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    for message in messages:
        if (
            deduped
            and deduped[-1].get("role") == message.get("role")
            and deduped[-1].get("content") == message.get("content")
        ):
            continue
        deduped.append(message)
    return deduped


def _build_turn_embedding_payload(
    turn_messages: list[tuple[int, dict[str, Any]]],
    *,
    turn_start_index: int,
    turn_end_index: int,
) -> tuple[str, dict[str, Any]] | None:
    normalized_messages: list[dict[str, str]] = []
    has_user = False
    has_assistant = False

    for _, message in turn_messages:
        role = _normalize_dialog_role(message.get("role"))
        if role is None:
            continue
        content = _message_to_text(message).strip()
        if not content:
            continue
        normalized_messages.append({"role": role, "content": content})
        if role == "user":
            has_user = True
        elif role == "assistant":
            has_assistant = True

    if not (has_user and has_assistant):
        return None

    text = "\n".join(f"{item['role']}: {item['content']}" for item in normalized_messages)
    metadata = {
        "messages": normalized_messages,
        "turn_start_index": turn_start_index,
        "turn_end_index": turn_end_index,
    }
    return text, metadata


def _collect_complete_turns_for_vector_index(
    messages: list[dict[str, Any]],
    *,
    start_index: int,
) -> tuple[list[str], list[dict[str, Any]], int, int]:
    n = len(messages)
    safe_start = max(0, min(int(start_index), n))
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []
    next_indexed_until = safe_start
    indexed_turn_count = 0

    i = safe_start
    while i < n:
        while i < n:
            message = messages[i]
            if isinstance(message, dict) and message.get("role") == "user":
                break
            i += 1
        next_indexed_until = i
        if i >= n:
            break

        turn_start = i
        i += 1
        while i < n:
            message = messages[i]
            if isinstance(message, dict) and message.get("role") == "user":
                break
            i += 1
        turn_end = i - 1
        is_tail = i >= n

        turn_messages: list[tuple[int, dict[str, Any]]] = []
        for idx in range(turn_start, turn_end + 1):
            msg = messages[idx]
            if isinstance(msg, dict):
                turn_messages.append((idx, msg))

        payload = _build_turn_embedding_payload(
            turn_messages,
            turn_start_index=turn_start,
            turn_end_index=turn_end,
        )
        if payload is not None:
            text, metadata = payload
            texts.append(text)
            metadatas.append(metadata)
            indexed_turn_count += 1
            next_indexed_until = turn_end + 1
            continue

        if is_tail:
            next_indexed_until = turn_start
            break

        next_indexed_until = turn_end + 1

    return texts, metadatas, next_indexed_until, indexed_turn_count


def _estimate_tokens_from_messages(messages: list[dict[str, Any]]) -> int:
    total_chars = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if isinstance(role, str):
            total_chars += len(role) + 1
        total_chars += len(_message_to_text(message))
    if total_chars <= 0:
        return 0
    return int(math.ceil(total_chars / 4.0))


def _split_turns(non_system_messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    turns: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for msg in non_system_messages:
        role = msg.get("role") if isinstance(msg, dict) else None
        if role == "user" and current:
            turns.append(current)
            current = [copy.deepcopy(msg)]
            continue
        current.append(copy.deepcopy(msg))
    if current:
        turns.append(current)
    return turns


def _flatten_turns(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for turn in turns:
        flattened.extend(_deepcopy_messages(turn))
    return flattened


def _split_system_and_non_system(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    systems: list[dict[str, Any]] = []
    non_system: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "system":
            systems.append(copy.deepcopy(msg))
        else:
            non_system.append(copy.deepcopy(msg))
    return systems, non_system


def _latest_user_query(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = _message_to_text(msg).strip()
            if text:
                return text
    return ""


def _merge_history_and_incoming(
    history: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    clean_history = _deepcopy_messages(history)
    clean_incoming = _deepcopy_messages(incoming)
    if not clean_history:
        return clean_incoming
    if not clean_incoming:
        return clean_history
    if (
        len(clean_incoming) >= len(clean_history)
        and clean_incoming[: len(clean_history)] == clean_history
    ):
        return clean_incoming
    incoming_systems, incoming_non_system = _split_system_and_non_system(clean_incoming)
    history_systems, history_non_system = _split_system_and_non_system(clean_history)
    systems = incoming_systems if incoming_systems else history_systems
    return systems + history_non_system + incoming_non_system


def _resolve_memory_namespace(session_id: str, memory_namespace: str | None) -> str:
    if isinstance(memory_namespace, str) and memory_namespace.strip():
        return memory_namespace.strip()
    if isinstance(session_id, str) and session_id.strip():
        return session_id.strip()
    return ""


def _normalize_profile_document(profile: Any) -> dict[str, Any]:
    return copy.deepcopy(profile) if isinstance(profile, dict) else {}


# Recursive merge keeps existing nested keys unless the patch overwrites them.
def _merge_profile_documents(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(key, str) and isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_profile_documents(merged[key], value)
        elif isinstance(key, str):
            merged[key] = copy.deepcopy(value)
    return merged


def _flatten_turn_messages(metadatas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for metadata in metadatas:
        raw_messages = metadata.get("messages")
        if not isinstance(raw_messages, list):
            continue
        for message in raw_messages:
            if not isinstance(message, dict):
                continue
            role = _normalize_dialog_role(message.get("role"))
            content = _content_to_text(message.get("content")).strip()
            if role is None or not content:
                continue
            messages.append({"role": role, "content": content})
    return messages


def _normalize_long_term_text(item: Any, *, max_chars: int = 2_000) -> str:
    text = _content_to_text(item).strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _infer_long_term_memory_type(item: dict[str, Any]) -> str:
    raw_type = str(item.get("memory_type") or item.get("type") or "").strip().lower()
    if raw_type in {"fact", "episode", "playbook"}:
        return raw_type
    if "steps" in item or "trigger" in item or "goal" in item:
        return "playbook"
    if "situation" in item or "action" in item or "outcome" in item:
        return "episode"
    return "fact"


def _normalize_long_term_fact_item(item: Any) -> dict[str, str] | None:
    if isinstance(item, str):
        text = item.strip()
        return {"memory_type": "fact", "subtype": "fact", "text": text} if text else None
    if not isinstance(item, dict):
        return None
    text = _normalize_long_term_text(item.get("text") if "text" in item else item.get("content"))
    if not text:
        return None
    raw_subtype = item.get("subtype") if "subtype" in item else item.get("kind")
    subtype = raw_subtype.strip().lower() if isinstance(raw_subtype, str) else "fact"
    if subtype not in {"fact", "decision", "project_context", "entity", "event"}:
        subtype = "fact"
    normalized = {"memory_type": "fact", "subtype": subtype, "text": text}
    for key in ("source", "created_at"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    return normalized


def _normalize_long_term_facts(items: Any, max_items: int) -> list[dict[str, str]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        fact = _normalize_long_term_fact_item(item)
        if fact is None:
            continue
        key = (fact["subtype"], fact["text"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(fact)
        if len(normalized) >= max(0, int(max_items)):
            break
    return normalized


def _format_episode_memory_text(item: dict[str, Any]) -> str:
    explicit_text = _normalize_long_term_text(item.get("text"))
    if explicit_text:
        return explicit_text
    parts = []
    situation = _normalize_long_term_text(item.get("situation"), max_chars=600)
    action = _normalize_long_term_text(item.get("action"), max_chars=600)
    outcome = _normalize_long_term_text(item.get("outcome"), max_chars=600)
    if situation:
        parts.append(f"Situation: {situation}")
    if action:
        parts.append(f"Action: {action}")
    if outcome:
        parts.append(f"Outcome: {outcome}")
    return "\n".join(parts).strip()


def _normalize_long_term_episode_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text = _format_episode_memory_text(item)
    if not text:
        return None
    normalized: dict[str, Any] = {
        "memory_type": "episode",
        "text": text,
    }
    for key in ("situation", "action", "outcome", "title"):
        value = _normalize_long_term_text(item.get(key), max_chars=600)
        if value:
            normalized[key] = value
    for key in ("source", "created_at"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    return normalized


def _normalize_long_term_episodes(items: Any, max_items: int) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        episode = _normalize_long_term_episode_item(item)
        if episode is None:
            continue
        key = episode["text"]
        if key in seen:
            continue
        seen.add(key)
        normalized.append(episode)
        if len(normalized) >= max(0, int(max_items)):
            break
    return normalized


def _format_playbook_memory_text(item: dict[str, Any]) -> str:
    explicit_text = _normalize_long_term_text(item.get("text"), max_chars=2_400)
    if explicit_text:
        return explicit_text
    parts = []
    trigger = _normalize_long_term_text(item.get("trigger"), max_chars=400)
    goal = _normalize_long_term_text(item.get("goal"), max_chars=400)
    steps = item.get("steps")
    caveats = _normalize_long_term_text(item.get("caveats"), max_chars=600)
    if trigger:
        parts.append(f"Trigger: {trigger}")
    if goal:
        parts.append(f"Goal: {goal}")
    if isinstance(steps, list):
        normalized_steps = [
            _normalize_long_term_text(step, max_chars=400)
            for step in steps
            if _normalize_long_term_text(step, max_chars=400)
        ]
        if normalized_steps:
            parts.append("Steps:")
            parts.extend(f"{idx + 1}. {step}" for idx, step in enumerate(normalized_steps[:8]))
    if caveats:
        parts.append(f"Caveats: {caveats}")
    return "\n".join(parts).strip()


def _normalize_long_term_playbook_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    text = _format_playbook_memory_text(item)
    if not text:
        return None
    normalized: dict[str, Any] = {
        "memory_type": "playbook",
        "text": text,
    }
    for key in ("trigger", "goal", "caveats", "title"):
        value = _normalize_long_term_text(item.get(key), max_chars=600)
        if value:
            normalized[key] = value
    steps = item.get("steps")
    if isinstance(steps, list):
        normalized_steps = [
            _normalize_long_term_text(step, max_chars=400)
            for step in steps
            if _normalize_long_term_text(step, max_chars=400)
        ]
        if normalized_steps:
            normalized["steps"] = normalized_steps[:8]
    for key in ("source", "created_at"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            normalized[key] = value.strip()
    return normalized


def _normalize_long_term_playbooks(items: Any, max_items: int) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        playbook = _normalize_long_term_playbook_item(item)
        if playbook is None:
            continue
        key = playbook["text"]
        if key in seen:
            continue
        seen.add(key)
        normalized.append(playbook)
        if len(normalized) >= max(0, int(max_items)):
            break
    return normalized


def _normalize_long_term_search_results(
    items: Any,
    *,
    max_items: int,
    memory_type: str,
) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        inferred_type = _infer_long_term_memory_type(item)
        if inferred_type != memory_type:
            continue
        filtered.append(item)
    if memory_type == "fact":
        return _normalize_long_term_facts(filtered, max_items)
    if memory_type == "episode":
        return _normalize_long_term_episodes(filtered, max_items)
    if memory_type == "playbook":
        return _normalize_long_term_playbooks(filtered, max_items)
    return []


def _build_long_term_memory_payloads(
    *,
    facts: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    playbooks: list[dict[str, Any]],
    session_id: str,
    memory_namespace: str,
    turn_metadatas: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    if not facts and not episodes and not playbooks:
        return [], []
    first_turn = min((meta.get("turn_start_index", 0) for meta in turn_metadatas if isinstance(meta, dict)), default=0)
    last_turn = max((meta.get("turn_end_index", 0) for meta in turn_metadatas if isinstance(meta, dict)), default=0)
    created_at = _current_timestamp()
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []
    for item in list(facts) + list(episodes) + list(playbooks):
        memory_type = _infer_long_term_memory_type(item) if isinstance(item, dict) else ""
        text = _normalize_long_term_text(item.get("text") if isinstance(item, dict) else "")
        if memory_type not in {"fact", "episode", "playbook"} or not text:
            continue
        texts.append(text)
        metadata: dict[str, Any] = {
            "memory_type": memory_type,
            "kind": item.get("subtype", memory_type) if isinstance(item, dict) else memory_type,
            "session_id": session_id,
            "memory_namespace": memory_namespace,
            "turn_start_index": first_turn,
            "turn_end_index": last_turn,
            "created_at": item.get("created_at", created_at) if isinstance(item, dict) else created_at,
            "source": item.get("source", "long_term_extractor") if isinstance(item, dict) else "long_term_extractor",
        }
        if isinstance(item, dict):
            for key in ("subtype", "title", "situation", "action", "outcome", "trigger", "goal", "caveats"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    metadata[key] = value.strip()
            steps = item.get("steps")
            if isinstance(steps, list):
                normalized_steps = [step for step in steps if isinstance(step, str) and step.strip()]
                if normalized_steps:
                    metadata["steps"] = normalized_steps[:8]
        metadatas.append(metadata)
    return texts, metadatas


def _profile_priority(key: str) -> tuple[int, str]:
    normalized = str(key or "").strip().lower()
    if normalized in {"hard_constraints", "constraints"}:
        return (0, normalized)
    if normalized == "preferences":
        return (1, normalized)
    if normalized == "communication_style":
        return (2, normalized)
    if normalized == "identity":
        return (3, normalized)
    return (10, normalized)


def _select_profile_for_context(profile: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    if not profile:
        return {}
    rendered = json.dumps(profile, ensure_ascii=False)
    if len(rendered) <= max_chars:
        return profile
    selected: dict[str, Any] = {}
    for key in sorted(profile.keys(), key=_profile_priority):
        candidate = {**selected, key: profile[key]}
        if len(json.dumps(candidate, ensure_ascii=False)) > max_chars:
            continue
        selected[key] = copy.deepcopy(profile[key])
    return selected or {}


def _should_recall_episode_memories(query: str) -> bool:
    normalized = query.strip().lower()
    if not normalized:
        return False
    hints = (
        "before",
        "previous",
        "last time",
        "again",
        "similar",
        "history",
        "historical",
        "remember",
        "之前",
        "以前",
        "上次",
        "类似",
        "历史",
        "记得",
    )
    return any(hint in normalized for hint in hints)


def _should_recall_playbooks(query: str) -> bool:
    normalized = query.strip().lower()
    if not normalized:
        return False
    hints = (
        "how to",
        "how do",
        "steps",
        "process",
        "procedure",
        "workflow",
        "plan",
        "debug",
        "troubleshoot",
        "fix",
        "resolve",
        "repair",
        "排查",
        "步骤",
        "流程",
        "方案",
        "修复",
        "如何",
        "怎么做",
        "调试",
        "排错",
    )
    return any(hint in normalized for hint in hints)


def _call_long_term_extractor(
    extractor: LongTermExtractor,
    *,
    previous_profile: dict[str, Any],
    extraction_messages: list[dict[str, Any]],
    long_term: LongTermMemoryConfig,
    model: str,
) -> dict[str, Any]:
    try:
        parameters = inspect.signature(extractor).parameters
    except Exception:
        parameters = {}

    if "config" in parameters:
        return extractor(
            previous_profile,
            extraction_messages,
            long_term.max_profile_chars,
            long_term.max_fact_items,
            model,
            config=long_term,
        )
    if "long_term_config" in parameters:
        return extractor(
            previous_profile,
            extraction_messages,
            long_term.max_profile_chars,
            long_term.max_fact_items,
            model,
            long_term_config=long_term,
        )
    return extractor(
        previous_profile,
        extraction_messages,
        long_term.max_profile_chars,
        long_term.max_fact_items,
        model,
    )


def _call_vector_similarity_search(
    adapter: VectorStoreAdapter,
    *,
    session_id: str,
    query: str,
    k: int,
    min_score: float | None,
) -> list[str | dict[str, Any]]:
    try:
        parameters = inspect.signature(adapter.similarity_search).parameters
    except Exception:
        parameters = {}

    kwargs: dict[str, Any] = {
        "session_id": session_id,
        "query": query,
        "k": k,
    }
    if "min_score" in parameters:
        kwargs["min_score"] = min_score
    return adapter.similarity_search(**kwargs)


def _call_long_term_similarity_search(
    adapter: LongTermVectorAdapter,
    *,
    namespace: str,
    query: str,
    k: int,
    filters: dict[str, Any] | None = None,
    min_score: float | None,
) -> list[dict[str, Any]]:
    try:
        parameters = inspect.signature(adapter.similarity_search).parameters
    except Exception:
        parameters = {}

    kwargs: dict[str, Any] = {
        "namespace": namespace,
        "query": query,
        "k": k,
        "filters": filters,
    }
    if "min_score" in parameters:
        kwargs["min_score"] = min_score
    return adapter.similarity_search(**kwargs)


def _current_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class LastNTurnsStrategy:
    def __init__(self, last_n_turns: int = 8):
        self.last_n_turns = max(0, int(last_n_turns))

    def prepare(
        self,
        *,
        state: dict[str, Any],
        incoming: list[dict[str, Any]],
        max_context_window_tokens: int,
        model: str,
    ) -> list[dict[str, Any]]:
        del max_context_window_tokens, model
        systems, non_system = _split_system_and_non_system(incoming)
        turns = _split_turns(non_system)
        kept_turns = turns[-self.last_n_turns :] if self.last_n_turns > 0 else []
        output = systems + _flatten_turns(kept_turns)

        memory_meta = state.setdefault("_memory_meta", {})
        memory_meta["last_n_turns"] = self.last_n_turns
        memory_meta["kept_turn_count"] = len(kept_turns)
        memory_meta["total_turn_count"] = len(turns)
        return output

    def commit(
        self,
        *,
        state: dict[str, Any],
        full_conversation: list[dict[str, Any]],
    ) -> None:
        del state, full_conversation


class SummaryTokenStrategy:
    def __init__(
        self,
        *,
        summary_trigger_pct: float = 0.75,
        summary_target_pct: float = 0.45,
        max_summary_chars: int = 2400,
    ):
        self.summary_trigger_pct = float(summary_trigger_pct)
        self.summary_target_pct = float(summary_target_pct)
        self.max_summary_chars = max(256, int(max_summary_chars))

    def prepare(
        self,
        *,
        state: dict[str, Any],
        incoming: list[dict[str, Any]],
        max_context_window_tokens: int,
        model: str,
    ) -> list[dict[str, Any]]:
        output = _deepcopy_messages(incoming)
        memory_meta = state.setdefault("_memory_meta", {})
        memory_meta["summary_triggered"] = False

        if max_context_window_tokens <= 0:
            memory_meta["summary_skip_reason"] = "invalid_max_context_window"
            return output

        estimated_tokens = _estimate_tokens_from_messages(output)
        usage_pct = (estimated_tokens / max_context_window_tokens) if max_context_window_tokens > 0 else 0.0
        memory_meta["summary_usage_pct"] = round(usage_pct, 4)

        if usage_pct < self.summary_trigger_pct:
            memory_meta["summary_skip_reason"] = "below_trigger_threshold"
            return output

        systems, non_system = _split_system_and_non_system(output)
        turns = _split_turns(non_system)
        if len(turns) <= 1:
            memory_meta["summary_skip_reason"] = "insufficient_turns"
            return output

        target_tokens = max(1, int(max_context_window_tokens * self.summary_target_pct))
        summary_budget_tokens = max(32, int(math.ceil(self.max_summary_chars / 4.0)))
        running_tokens = _estimate_tokens_from_messages(systems) + summary_budget_tokens

        kept_turns: list[list[dict[str, Any]]] = []
        for turn in reversed(turns):
            turn_tokens = _estimate_tokens_from_messages(turn)
            if kept_turns and running_tokens + turn_tokens > target_tokens:
                break
            kept_turns.insert(0, _deepcopy_messages(turn))
            running_tokens += turn_tokens

        if len(kept_turns) >= len(turns):
            memory_meta["summary_skip_reason"] = "no_old_turns_to_summarize"
            return output

        old_turn_count = len(turns) - len(kept_turns)
        old_messages = _flatten_turns(turns[:old_turn_count])
        previous_summary = str(state.get("summary", ""))
        summary_generator = state.get("_summary_generator")
        if not callable(summary_generator):
            memory_meta["summary_fallback_reason"] = "summary_generator_missing"
            return output

        try:
            summary_text = summary_generator(
                previous_summary,
                old_messages,
                self.max_summary_chars,
                model,
            )
        except Exception as exc:
            memory_meta["summary_fallback_reason"] = f"summary_generation_failed: {exc}"
            return output

        summary_text = (summary_text or "").strip()
        if not summary_text:
            memory_meta["summary_fallback_reason"] = "empty_summary"
            return output

        if len(summary_text) > self.max_summary_chars:
            summary_text = summary_text[: self.max_summary_chars].rstrip()

        state["summary"] = summary_text
        summary_message = {"role": "system", "content": f"[MEMORY SUMMARY]\n{summary_text}"}
        summarized_output = systems + [summary_message] + _flatten_turns(kept_turns)

        memory_meta["summary_triggered"] = True
        memory_meta["summary_old_turn_count"] = old_turn_count
        memory_meta["summary_kept_turn_count"] = len(kept_turns)
        memory_meta["summary_target_tokens"] = target_tokens
        return summarized_output

    def commit(
        self,
        *,
        state: dict[str, Any],
        full_conversation: list[dict[str, Any]],
    ) -> None:
        del state, full_conversation


class HybridContextStrategy:
    def __init__(
        self,
        *,
        summary_strategy: SummaryTokenStrategy,
        last_n_strategy: LastNTurnsStrategy,
        vector_top_k: int = 4,
        vector_min_score: float | None = None,
        vector_adapter: VectorStoreAdapter | None = None,
    ):
        self.summary_strategy = summary_strategy
        self.last_n_strategy = last_n_strategy
        self.vector_top_k = max(0, int(vector_top_k))
        self.vector_min_score = float(vector_min_score) if vector_min_score is not None else None
        self.vector_adapter = vector_adapter

    def prepare(
        self,
        *,
        state: dict[str, Any],
        incoming: list[dict[str, Any]],
        max_context_window_tokens: int,
        model: str,
    ) -> list[dict[str, Any]]:
        summarized = self.summary_strategy.prepare(
            state=state,
            incoming=incoming,
            max_context_window_tokens=max_context_window_tokens,
            model=model,
        )
        trimmed = self.last_n_strategy.prepare(
            state=state,
            incoming=summarized,
            max_context_window_tokens=max_context_window_tokens,
            model=model,
        )
        output = _deepcopy_messages(trimmed)

        adapter = self.vector_adapter
        session_id = str(state.get("_session_id", "") or "")
        if adapter is None or self.vector_top_k <= 0 or not session_id:
            return output

        query = _latest_user_query(output)
        if not query:
            return output

        memory_meta = state.setdefault("_memory_meta", {})
        try:
            recalled = _call_vector_similarity_search(
                adapter,
                session_id=session_id,
                query=query,
                k=self.vector_top_k,
                min_score=self.vector_min_score,
            )
        except Exception as exc:
            memory_meta["vector_fallback_reason"] = f"vector_search_failed: {exc}"
            return output

        normalized_recalled = _normalize_recalled_messages(recalled)
        if normalized_recalled:
            normalized_recalled = _infer_recall_roles_from_messages(normalized_recalled, incoming)
        recall_messages = _dedupe_adjacent_messages(_coerce_recall_messages(normalized_recalled))
        if not recall_messages:
            return output

        systems, non_system = _split_system_and_non_system(output)
        recall_message = {
            "role": "system",
            "content": f"[Recall messages]\n{json.dumps(recall_messages, ensure_ascii=False)}",
        }
        memory_meta["vector_recall_hit_count"] = len(recalled) if isinstance(recalled, list) else 0
        memory_meta["vector_recall_count"] = len(recall_messages)
        memory_meta["vector_recall_with_role_count"] = len(recall_messages)
        return systems + [recall_message] + non_system

    def commit(
        self,
        *,
        state: dict[str, Any],
        full_conversation: list[dict[str, Any]],
    ) -> None:
        self.summary_strategy.commit(state=state, full_conversation=full_conversation)
        self.last_n_strategy.commit(state=state, full_conversation=full_conversation)


class MemoryManager:
    def __init__(
        self,
        config: MemoryConfig | None = None,
        store: SessionStore | None = None,
        strategy: ContextStrategy | None = None,
    ):
        self.config = config or MemoryConfig()
        self.store = store or InMemorySessionStore()
        self.strategy = strategy or HybridContextStrategy(
            summary_strategy=SummaryTokenStrategy(
                summary_trigger_pct=self.config.summary_trigger_pct,
                summary_target_pct=self.config.summary_target_pct,
                max_summary_chars=self.config.max_summary_chars,
            ),
            last_n_strategy=LastNTurnsStrategy(last_n_turns=self.config.last_n_turns),
            vector_top_k=self.config.vector_top_k,
            vector_min_score=self.config.vector_min_score,
            vector_adapter=self.config.vector_adapter,
        )
        self._last_prepare_info: dict[str, Any] = {}
        self._last_commit_info: dict[str, Any] = {}

    @property
    def last_prepare_info(self) -> dict[str, Any]:
        return copy.deepcopy(self._last_prepare_info)

    @property
    def last_commit_info(self) -> dict[str, Any]:
        return copy.deepcopy(self._last_commit_info)

    def ensure_long_term_components(self, *, broth_instance: Any | None = None) -> None:
        long_term = self.config.long_term
        if long_term is None:
            return

        if long_term.profile_store is None:
            long_term.profile_store = JsonFileLongTermProfileStore(base_dir=long_term.profile_base_dir)

        if long_term.vector_adapter is not None:
            return

        from .memory_qdrant import build_default_long_term_qdrant_vector_adapter

        qdrant_path = Path(long_term.qdrant_path) if long_term.qdrant_path is not None else (_default_user_data_dir() / "qdrant_long_term")
        long_term.vector_adapter = build_default_long_term_qdrant_vector_adapter(
            broth_instance=broth_instance,
            model=long_term.embedding_model,
            payload=long_term.embedding_payload,
            path=qdrant_path,
            collection_prefix=long_term.collection_prefix,
        )

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return _estimate_tokens_from_messages(_deepcopy_messages(messages))

    def _prepare_long_term_messages(
        self,
        *,
        state: dict[str, Any],
        prepared: list[dict[str, Any]],
        session_id: str,
        memory_namespace: str | None,
    ) -> list[dict[str, Any]]:
        long_term = self.config.long_term
        if long_term is None:
            return prepared

        namespace = _resolve_memory_namespace(session_id, memory_namespace)
        if not namespace:
            return prepared

        self.ensure_long_term_components()
        memory_meta = state.setdefault("_memory_meta", {})
        memory_meta["memory_namespace"] = namespace

        systems, non_system = _split_system_and_non_system(prepared)
        injected: list[dict[str, Any]] = []

        profile_store = long_term.profile_store
        if profile_store is not None:
            try:
                profile = _normalize_profile_document(profile_store.load(namespace))
            except Exception as exc:
                memory_meta["long_term_profile_fallback_reason"] = f"profile_load_failed: {exc}"
            else:
                selected_profile = _select_profile_for_context(
                    profile,
                    max_chars=long_term.max_profile_chars,
                )
                if selected_profile:
                    injected.append({
                        "role": "system",
                        "content": f"[MEMORY_PROFILE]\n{json.dumps(selected_profile, ensure_ascii=False)}",
                    })
                    memory_meta["long_term_profile_applied"] = True
                    memory_meta["long_term_profile_key_count"] = len(selected_profile)

        query = _latest_user_query(prepared)
        if query and long_term.vector_adapter is not None:
            try:
                if long_term.vector_top_k > 0:
                    recalled_facts = _call_long_term_similarity_search(
                        long_term.vector_adapter,
                        namespace=namespace,
                        query=query,
                        k=long_term.vector_top_k,
                        filters={"memory_type": "fact"},
                        min_score=(
                            float(long_term.vector_min_score)
                            if long_term.vector_min_score is not None
                            else None
                        ),
                    )
                else:
                    recalled_facts = []
            except Exception as exc:
                memory_meta["long_term_vector_fallback_reason"] = f"vector_search_failed: {exc}"
            else:
                facts = _normalize_long_term_search_results(
                    recalled_facts,
                    max_items=long_term.vector_top_k,
                    memory_type="fact",
                )
                if facts:
                    injected.append({
                        "role": "system",
                        "content": f"[MEMORY_FACTS]\n{json.dumps(facts, ensure_ascii=False)}",
                    })
                    memory_meta["long_term_fact_recall_hit_count"] = len(recalled_facts) if isinstance(recalled_facts, list) else 0
                    memory_meta["long_term_fact_recall_count"] = len(facts)

        if (
            query
            and long_term.vector_adapter is not None
            and long_term.episode_top_k > 0
            and _should_recall_episode_memories(query)
        ):
            try:
                recalled_episodes = _call_long_term_similarity_search(
                    long_term.vector_adapter,
                    namespace=namespace,
                    query=query,
                    k=long_term.episode_top_k,
                    filters={"memory_type": "episode"},
                    min_score=(
                        float(long_term.episode_min_score)
                        if long_term.episode_min_score is not None
                        else None
                    ),
                )
            except Exception as exc:
                memory_meta["long_term_episode_fallback_reason"] = f"episode_search_failed: {exc}"
            else:
                episodes = _normalize_long_term_search_results(
                    recalled_episodes,
                    max_items=long_term.episode_top_k,
                    memory_type="episode",
                )
                if episodes:
                    injected.append({
                        "role": "system",
                        "content": f"[MEMORY_EPISODES]\n{json.dumps(episodes, ensure_ascii=False)}",
                    })
                    memory_meta["long_term_episode_recall_count"] = len(episodes)

        if (
            query
            and long_term.vector_adapter is not None
            and long_term.playbook_top_k > 0
            and _should_recall_playbooks(query)
        ):
            try:
                recalled_playbooks = _call_long_term_similarity_search(
                    long_term.vector_adapter,
                    namespace=namespace,
                    query=query,
                    k=long_term.playbook_top_k,
                    filters={"memory_type": "playbook"},
                    min_score=(
                        float(long_term.playbook_min_score)
                        if long_term.playbook_min_score is not None
                        else None
                    ),
                )
            except Exception as exc:
                memory_meta["long_term_playbook_fallback_reason"] = f"playbook_search_failed: {exc}"
            else:
                playbooks = _normalize_long_term_search_results(
                    recalled_playbooks,
                    max_items=long_term.playbook_top_k,
                    memory_type="playbook",
                )
                if playbooks:
                    injected.append({
                        "role": "system",
                        "content": f"[MEMORY_PLAYBOOKS]\n{json.dumps(playbooks, ensure_ascii=False)}",
                    })
                    memory_meta["long_term_playbook_recall_count"] = len(playbooks)

        if not injected:
            return prepared
        return systems + injected + non_system

    def prepare_messages(
        self,
        session_id: str,
        incoming: list[dict[str, Any]],
        *,
        max_context_window_tokens: int,
        model: str,
        summary_generator: SummaryGenerator | None = None,
        memory_namespace: str | None = None,
    ) -> list[dict[str, Any]]:
        state = self.store.load(session_id) if session_id else {}
        history = state.get("messages", [])
        if not isinstance(history, list):
            history = []

        merged = _merge_history_and_incoming(history, incoming)
        before_tokens = self.estimate_tokens(merged)

        state["_memory_meta"] = {}
        state["_session_id"] = session_id
        state["_memory_model"] = model
        if summary_generator is not None:
            state["_summary_generator"] = summary_generator

        try:
            prepared = self.strategy.prepare(
                state=state,
                incoming=merged,
                max_context_window_tokens=max_context_window_tokens,
                model=model,
            )
            prepared = self._prepare_long_term_messages(
                state=state,
                prepared=_deepcopy_messages(prepared),
                session_id=session_id,
                memory_namespace=memory_namespace,
            )
        finally:
            state.pop("_summary_generator", None)
            state.pop("_memory_model", None)
            state.pop("_session_id", None)

        prepared = _deepcopy_messages(prepared)
        after_tokens = self.estimate_tokens(prepared)
        memory_meta = state.pop("_memory_meta", {})

        self._last_prepare_info = {
            "session_id": session_id,
            "memory_namespace": _resolve_memory_namespace(session_id, memory_namespace),
            "before_estimated_tokens": before_tokens,
            "after_estimated_tokens": after_tokens,
            **(memory_meta if isinstance(memory_meta, dict) else {}),
        }
        if session_id:
            self.store.save(session_id, state)
        return prepared

    def _commit_long_term_memory(
        self,
        *,
        state: dict[str, Any],
        session_id: str,
        clean_conversation: list[dict[str, Any]],
        commit_info: dict[str, Any],
        memory_namespace: str | None,
        model: str | None,
        long_term_extractor: LongTermExtractor | None,
    ) -> None:
        long_term = self.config.long_term
        if long_term is None:
            return

        namespace = _resolve_memory_namespace(session_id, memory_namespace)
        if not namespace:
            return

        self.ensure_long_term_components()
        commit_info["memory_namespace"] = namespace

        indexed_until_raw = state.get("long_term_indexed_until", 0)
        indexed_until = int(indexed_until_raw) if isinstance(indexed_until_raw, int) else 0
        if indexed_until < 0 or indexed_until > len(clean_conversation):
            indexed_until = 0

        _, turn_metadatas, next_indexed_until, indexed_turn_count = _collect_complete_turns_for_vector_index(
            clean_conversation,
            start_index=indexed_until,
        )
        if not turn_metadatas:
            state["long_term_indexed_until"] = next_indexed_until
            state["long_term_pending_turn_count"] = 0
            return

        pending_turn_count = max(0, indexed_turn_count)
        extract_every_n_turns = max(1, int(long_term.extract_every_n_turns or 1))
        state["long_term_pending_turn_count"] = pending_turn_count
        commit_info["long_term_pending_turn_count"] = pending_turn_count
        commit_info["long_term_extract_every_n_turns"] = extract_every_n_turns

        if pending_turn_count < extract_every_n_turns:
            commit_info["long_term_extraction_deferred"] = True
            return

        extractor = long_term_extractor or long_term.extractor
        if not callable(extractor):
            commit_info["long_term_fallback_reason"] = "long_term_extractor_missing"
            return

        profile_store = long_term.profile_store
        if profile_store is None:
            commit_info["long_term_profile_fallback_reason"] = "profile_store_missing"
            return

        try:
            previous_profile = _normalize_profile_document(profile_store.load(namespace))
        except Exception as exc:
            commit_info["long_term_profile_fallback_reason"] = f"profile_load_failed: {exc}"
            return

        extraction_messages = _flatten_turn_messages(turn_metadatas)
        try:
            extraction = _call_long_term_extractor(
                extractor,
                previous_profile=previous_profile,
                extraction_messages=extraction_messages,
                long_term=long_term,
                model=model or "",
            )
        except Exception as exc:
            commit_info["long_term_extractor_fallback_reason"] = f"long_term_extraction_failed: {exc}"
            return

        extraction = extraction if isinstance(extraction, dict) else {}
        profile_patch = _normalize_profile_document(extraction.get("profile_patch"))
        facts = _normalize_long_term_facts(extraction.get("facts"), long_term.max_fact_items)
        episodes = _normalize_long_term_episodes(extraction.get("episodes"), long_term.max_episode_items)
        playbooks = _normalize_long_term_playbooks(extraction.get("playbooks"), long_term.max_playbook_items)

        advance_cursor = True
        did_write = False

        if profile_patch:
            did_write = True
            merged_profile = _merge_profile_documents(previous_profile, profile_patch)
            try:
                profile_store.save(namespace, merged_profile)
                commit_info["long_term_profile_updated"] = True
                commit_info["long_term_profile_key_count"] = len(merged_profile)
            except Exception as exc:
                commit_info["long_term_profile_fallback_reason"] = f"profile_save_failed: {exc}"
                advance_cursor = False

        if (facts or episodes or playbooks) and long_term.vector_adapter is not None:
            did_write = True
            texts, metadatas = _build_long_term_memory_payloads(
                facts=facts,
                episodes=episodes,
                playbooks=playbooks,
                session_id=session_id,
                memory_namespace=namespace,
                turn_metadatas=turn_metadatas,
            )
            if texts:
                try:
                    long_term.vector_adapter.add_texts(
                        namespace=namespace,
                        texts=texts,
                        metadatas=metadatas,
                    )
                    fact_count = len([meta for meta in metadatas if meta.get("memory_type") == "fact"])
                    episode_count = len([meta for meta in metadatas if meta.get("memory_type") == "episode"])
                    playbook_count = len([meta for meta in metadatas if meta.get("memory_type") == "playbook"])
                    commit_info["long_term_fact_indexed_count"] = fact_count
                    commit_info["long_term_episode_indexed_count"] = episode_count
                    commit_info["long_term_playbook_indexed_count"] = playbook_count
                    commit_info["long_term_memory_indexed_count"] = len(texts)
                except Exception as exc:
                    commit_info["long_term_vector_fallback_reason"] = f"vector_index_failed: {exc}"
                    advance_cursor = False

        if not did_write:
            commit_info["long_term_noop"] = True

        if advance_cursor:
            state["long_term_indexed_until"] = next_indexed_until
            state["long_term_pending_turn_count"] = 0
            commit_info["long_term_indexed_turn_count"] = indexed_turn_count

    def commit_messages(
        self,
        session_id: str,
        full_conversation: list[dict[str, Any]],
        *,
        memory_namespace: str | None = None,
        model: str | None = None,
        long_term_extractor: LongTermExtractor | None = None,
    ) -> None:
        state = self.store.load(session_id) if session_id else {}
        clean_conversation = _deepcopy_messages(full_conversation)
        state["messages"] = clean_conversation

        self.strategy.commit(state=state, full_conversation=clean_conversation)

        commit_info: dict[str, Any] = {
            "session_id": session_id,
            "memory_namespace": _resolve_memory_namespace(session_id, memory_namespace),
            "stored_message_count": len(clean_conversation),
        }

        adapter = self.config.vector_adapter
        if adapter is not None:
            indexed_until_raw = state.get("vector_indexed_until", 0)
            indexed_until = int(indexed_until_raw) if isinstance(indexed_until_raw, int) else 0
            if indexed_until < 0 or indexed_until > len(clean_conversation):
                indexed_until = 0
            texts, metadatas, next_indexed_until, indexed_turn_count = _collect_complete_turns_for_vector_index(
                clean_conversation,
                start_index=indexed_until,
            )

            if texts:
                try:
                    adapter.add_texts(
                        session_id=session_id,
                        texts=texts,
                        metadatas=metadatas,
                    )
                    state["vector_indexed_until"] = next_indexed_until
                    commit_info["vector_indexed_count"] = len(texts)
                    commit_info["vector_indexed_turn_count"] = indexed_turn_count
                except Exception as exc:
                    commit_info["vector_fallback_reason"] = f"vector_index_failed: {exc}"
            else:
                state["vector_indexed_until"] = next_indexed_until

        self._commit_long_term_memory(
            state=state,
            session_id=session_id,
            clean_conversation=clean_conversation,
            commit_info=commit_info,
            memory_namespace=memory_namespace,
            model=model,
            long_term_extractor=long_term_extractor,
        )

        if session_id:
            self.store.save(session_id, state)
        self._last_commit_info = commit_info


__all__ = [
    "ContextStrategy",
    "HybridContextStrategy",
    "InMemorySessionStore",
    "JsonFileLongTermProfileStore",
    "LastNTurnsStrategy",
    "LongTermExtractor",
    "LongTermMemoryConfig",
    "LongTermProfileStore",
    "LongTermVectorAdapter",
    "MemoryConfig",
    "MemoryManager",
    "SessionStore",
    "SummaryGenerator",
    "SummaryTokenStrategy",
    "VectorStoreAdapter",
]
