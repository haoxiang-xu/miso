from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


SummaryGenerator = Callable[[str, list[dict[str, Any]], int, str], str]


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
    ) -> list[str]:
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


@dataclass
class MemoryConfig:
    last_n_turns: int = 8
    summary_trigger_pct: float = 0.75
    summary_target_pct: float = 0.45
    max_summary_chars: int = 2400
    vector_top_k: int = 4
    vector_adapter: VectorStoreAdapter | None = None


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
    return clean_history + clean_incoming


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
        vector_adapter: VectorStoreAdapter | None = None,
    ):
        self.summary_strategy = summary_strategy
        self.last_n_strategy = last_n_strategy
        self.vector_top_k = max(0, int(vector_top_k))
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
            recalled = adapter.similarity_search(
                session_id=session_id,
                query=query,
                k=self.vector_top_k,
            )
        except Exception as exc:
            memory_meta["vector_fallback_reason"] = f"vector_search_failed: {exc}"
            return output

        cleaned_recalled = [item.strip() for item in recalled if isinstance(item, str) and item.strip()]
        if not cleaned_recalled:
            return output

        systems, non_system = _split_system_and_non_system(output)
        recall_lines = "\n".join(f"- {item}" for item in cleaned_recalled)
        recall_message = {
            "role": "system",
            "content": f"[MEMORY RECALL]\n{recall_lines}",
        }
        memory_meta["vector_recall_count"] = len(cleaned_recalled)
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

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        return _estimate_tokens_from_messages(_deepcopy_messages(messages))

    def prepare_messages(
        self,
        session_id: str,
        incoming: list[dict[str, Any]],
        *,
        max_context_window_tokens: int,
        model: str,
        summary_generator: SummaryGenerator | None = None,
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
        finally:
            state.pop("_summary_generator", None)
            state.pop("_memory_model", None)
            state.pop("_session_id", None)

        prepared = _deepcopy_messages(prepared)
        after_tokens = self.estimate_tokens(prepared)
        memory_meta = state.pop("_memory_meta", {})

        self._last_prepare_info = {
            "session_id": session_id,
            "before_estimated_tokens": before_tokens,
            "after_estimated_tokens": after_tokens,
            **(memory_meta if isinstance(memory_meta, dict) else {}),
        }
        self.store.save(session_id, state)
        return prepared

    def commit_messages(
        self,
        session_id: str,
        full_conversation: list[dict[str, Any]],
    ) -> None:
        state = self.store.load(session_id) if session_id else {}
        clean_conversation = _deepcopy_messages(full_conversation)
        state["messages"] = clean_conversation

        self.strategy.commit(state=state, full_conversation=clean_conversation)

        commit_info: dict[str, Any] = {
            "session_id": session_id,
            "stored_message_count": len(clean_conversation),
        }

        adapter = self.config.vector_adapter
        if adapter is not None:
            indexed_until_raw = state.get("vector_indexed_until", 0)
            indexed_until = int(indexed_until_raw) if isinstance(indexed_until_raw, int) else 0
            if indexed_until < 0 or indexed_until > len(clean_conversation):
                indexed_until = 0
            to_index = clean_conversation[indexed_until:]

            texts: list[str] = []
            metadatas: list[dict[str, Any]] = []
            for idx, msg in enumerate(to_index, start=indexed_until):
                role = msg.get("role")
                if role not in ("user", "assistant", "tool"):
                    continue
                text = _message_to_text(msg).strip()
                if not text:
                    continue
                texts.append(text)
                metadatas.append({"role": role, "index": idx})

            if texts:
                try:
                    adapter.add_texts(
                        session_id=session_id,
                        texts=texts,
                        metadatas=metadatas,
                    )
                    state["vector_indexed_until"] = len(clean_conversation)
                    commit_info["vector_indexed_count"] = len(texts)
                except Exception as exc:
                    commit_info["vector_fallback_reason"] = f"vector_index_failed: {exc}"
            else:
                state["vector_indexed_until"] = len(clean_conversation)

        self.store.save(session_id, state)
        self._last_commit_info = commit_info


__all__ = [
    "ContextStrategy",
    "HybridContextStrategy",
    "InMemorySessionStore",
    "LastNTurnsStrategy",
    "MemoryConfig",
    "MemoryManager",
    "SessionStore",
    "SummaryTokenStrategy",
    "VectorStoreAdapter",
]
