from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from .config import MemoryConfig
from .long_term import LongTermExtractor
from .manager import MemoryManager, SummaryGenerator
from .stores import InMemorySessionStore, SessionStore


def _deepcopy_messages(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [copy.deepcopy(message) for message in (messages or []) if isinstance(message, dict)]


def _split_system_and_non_system(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    systems: list[dict[str, Any]] = []
    non_system: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("role") == "system":
            systems.append(copy.deepcopy(message))
        else:
            non_system.append(copy.deepcopy(message))
    return systems, non_system


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
    if len(clean_incoming) >= len(clean_history) and clean_incoming[: len(clean_history)] == clean_history:
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


@dataclass
class KernelMemoryRuntime:
    memory_manager: MemoryManager
    summary_generator: SummaryGenerator | None = None
    long_term_extractor: LongTermExtractor | None = None
    last_prepare_info: dict[str, Any] = field(default_factory=dict)
    last_commit_info: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.long_term_extractor is None:
            long_term = getattr(self.memory_manager.config, "long_term", None)
            self.long_term_extractor = getattr(long_term, "extractor", None)

    @classmethod
    def from_memory_manager(
        cls,
        memory_manager: MemoryManager,
        *,
        summary_generator: SummaryGenerator | None = None,
        long_term_extractor: LongTermExtractor | None = None,
    ) -> "KernelMemoryRuntime":
        if not isinstance(memory_manager, MemoryManager):
            raise TypeError("memory_manager must be a MemoryManager")
        return cls(
            memory_manager=memory_manager,
            summary_generator=summary_generator,
            long_term_extractor=long_term_extractor,
        )

    @classmethod
    def from_config(
        cls,
        config: MemoryConfig | None = None,
        *,
        store: SessionStore | None = None,
        summary_generator: SummaryGenerator | None = None,
        long_term_extractor: LongTermExtractor | None = None,
    ) -> "KernelMemoryRuntime":
        manager = MemoryManager(
            config=copy.deepcopy(config) if config is not None else MemoryConfig(),
            store=store if store is not None else InMemorySessionStore(),
        )
        return cls.from_memory_manager(
            manager,
            summary_generator=summary_generator,
            long_term_extractor=long_term_extractor,
        )

    @property
    def config(self) -> MemoryConfig:
        return self.memory_manager.config

    @property
    def store(self) -> SessionStore:
        return self.memory_manager.store

    def ensure_long_term_components(self) -> None:
        try:
            self.memory_manager.ensure_long_term_components()
        except Exception:
            return

    def load_session_state(self, session_id: str) -> dict[str, Any]:
        if not session_id:
            return {}
        try:
            state = self.store.load(session_id)
        except Exception:
            return {}
        return copy.deepcopy(state) if isinstance(state, dict) else {}

    def save_session_state(self, session_id: str, state: dict[str, Any]) -> None:
        if not session_id:
            return
        self.store.save(session_id, copy.deepcopy(state if isinstance(state, dict) else {}))

    def bootstrap_session(
        self,
        *,
        session_id: str,
        memory_namespace: str | None,
        incoming_messages: list[dict[str, Any]],
        resume_mode: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any], str]:
        if not session_id:
            prepare_info = {
                "applied": False,
                "session_id": "",
                "memory_namespace": _resolve_memory_namespace(session_id, memory_namespace),
                "resume_mode": bool(resume_mode),
                "skip_reason": "missing_session_id",
            }
            self.last_prepare_info = copy.deepcopy(prepare_info)
            return _deepcopy_messages(incoming_messages), {}, prepare_info, ""

        self.ensure_long_term_components()
        loaded_state = self.load_session_state(session_id)
        history = loaded_state.get("messages", [])
        if not isinstance(history, list):
            history = []
        summary_text = str(loaded_state.get("summary", "") or "").strip()
        resolved_namespace = _resolve_memory_namespace(session_id, memory_namespace)
        merged_messages = (
            _deepcopy_messages(incoming_messages)
            if resume_mode
            else _merge_history_and_incoming(history, incoming_messages)
        )
        prepare_info = {
            "applied": True,
            "session_id": session_id,
            "memory_namespace": resolved_namespace,
            "resume_mode": bool(resume_mode),
            "loaded_message_count": len(history),
            "merged_message_count": len(merged_messages),
            "summary_present": bool(summary_text),
            "vector_indexed_until": int(loaded_state.get("vector_indexed_until") or 0),
            "long_term_indexed_until": int(loaded_state.get("long_term_indexed_until") or 0),
            "long_term_pending_turn_count": int(loaded_state.get("long_term_pending_turn_count") or 0),
        }
        self.last_prepare_info = copy.deepcopy(prepare_info)
        return merged_messages, loaded_state, prepare_info, summary_text

    def recall_profile(
        self,
        *,
        session_id: str,
        memory_namespace: str | None = None,
        max_chars: int | None = None,
    ) -> dict[str, Any]:
        self.ensure_long_term_components()
        return self.memory_manager.recall_profile(
            session_id=session_id,
            memory_namespace=memory_namespace,
            max_chars=max_chars,
        )

    def recall_memory(
        self,
        *,
        session_id: str,
        memory_namespace: str | None,
        query: str,
        include_short_term: bool,
        include_long_term: bool,
    ) -> dict[str, Any]:
        self.ensure_long_term_components()
        return self.memory_manager.recall_memory(
            session_id=session_id,
            memory_namespace=memory_namespace,
            query=query,
            include_short_term=include_short_term,
            include_long_term=include_long_term,
        )

    def recall_long_term(
        self,
        *,
        session_id: str,
        memory_namespace: str | None,
        query: str,
    ) -> dict[str, Any]:
        self.ensure_long_term_components()
        facts = self.memory_manager._long_term_recall_result(
            session_id=session_id,
            memory_namespace=memory_namespace,
            query=query,
            memory_type="fact",
            apply_query_hints=False,
        )
        episodes = self.memory_manager._long_term_recall_result(
            session_id=session_id,
            memory_namespace=memory_namespace,
            query=query,
            memory_type="episode",
            apply_query_hints=True,
        )
        playbooks = self.memory_manager._long_term_recall_result(
            session_id=session_id,
            memory_namespace=memory_namespace,
            query=query,
            memory_type="playbook",
            apply_query_hints=True,
        )
        output = {
            "facts": copy.deepcopy(facts.get("items", [])),
            "episodes": copy.deepcopy(episodes.get("items", [])),
            "playbooks": copy.deepcopy(playbooks.get("items", [])),
            "counts": {
                "facts": int(facts.get("count") or 0),
                "episodes": int(episodes.get("count") or 0),
                "playbooks": int(playbooks.get("count") or 0),
            },
            "hit_counts": {
                "facts": int(facts.get("hit_count") or 0),
                "episodes": int(episodes.get("hit_count") or 0),
                "playbooks": int(playbooks.get("hit_count") or 0),
            },
            "available": any(bool(item.get("available")) for item in (facts, episodes, playbooks)),
        }
        fallback_reasons = {
            "facts": str(facts.get("fallback_reason") or "").strip(),
            "episodes": str(episodes.get("fallback_reason") or "").strip(),
            "playbooks": str(playbooks.get("fallback_reason") or "").strip(),
        }
        if any(fallback_reasons.values()):
            output["fallback_reasons"] = {
                key: value
                for key, value in fallback_reasons.items()
                if value
            }
        return output

    def commit_transcript(
        self,
        *,
        session_id: str,
        transcript: list[dict[str, Any]],
        memory_namespace: str | None,
        model: str | None,
        summary_text: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not session_id:
            commit_info = {
                "applied": False,
                "session_id": "",
                "memory_namespace": _resolve_memory_namespace(session_id, memory_namespace),
                "skip_reason": "missing_session_id",
            }
            self.last_commit_info = copy.deepcopy(commit_info)
            return commit_info, {}

        self.ensure_long_term_components()
        self.memory_manager.commit_messages(
            session_id=session_id,
            full_conversation=_deepcopy_messages(transcript),
            memory_namespace=memory_namespace,
            model=model,
            long_term_extractor=self.long_term_extractor,
        )
        stored_state = self.load_session_state(session_id)
        normalized_summary = str(summary_text or "").strip()
        stored_state["summary"] = normalized_summary
        self.save_session_state(session_id, stored_state)

        commit_info = copy.deepcopy(self.memory_manager.last_commit_info)
        commit_info["applied"] = True
        commit_info["summary_persisted"] = bool(normalized_summary)
        commit_info["summary_length"] = len(normalized_summary)
        self.last_commit_info = copy.deepcopy(commit_info)
        return commit_info, stored_state

    def build_default_components(self) -> list[Any]:
        from ..optimizers import (
            LastNOptimizer,
            LastNOptimizerConfig,
            LlmSummaryOptimizer,
            LlmSummaryOptimizerConfig,
            SlidingWindowOptimizer,
            SlidingWindowOptimizerConfig,
            ToolHistoryCompactionOptimizer,
            ToolHistoryCompactionOptimizerConfig,
        )
        from .bootstrap import MemoryBootstrapHarness
        from .commit import MemoryCommitHarness
        from .recall_long_term import LongTermRecallMemoryHarness
        from .short_term import ShortTermRecallMemoryHarness

        config = self.config
        return [
            ToolHistoryCompactionOptimizer(
                ToolHistoryCompactionOptimizerConfig(
                    enabled=bool(config.deferred_tool_compaction_enabled),
                    keep_completed_turns=int(config.deferred_tool_compaction_keep_completed_turns),
                    max_chars=int(config.deferred_tool_compaction_max_chars),
                    preview_chars=int(config.deferred_tool_compaction_preview_chars),
                    include_tools=copy.deepcopy(config.deferred_tool_compaction_include_tools),
                    hash_payloads=bool(config.deferred_tool_compaction_hash_payloads),
                )
            ),
            LlmSummaryOptimizer(
                LlmSummaryOptimizerConfig(
                    summary_trigger_pct=float(config.summary_trigger_pct),
                    summary_target_pct=float(config.summary_target_pct),
                    max_summary_chars=int(config.max_summary_chars),
                    summary_generator=self.summary_generator,
                )
            ),
            LastNOptimizer(
                LastNOptimizerConfig(last_n_turns=int(config.last_n_turns))
            ),
            SlidingWindowOptimizer(
                SlidingWindowOptimizerConfig(
                    max_window_pct=float(config.sliding_window_pct),
                    max_window_tokens=config.sliding_window_max_tokens,
                )
            ),
            ShortTermRecallMemoryHarness(runtime=self),
            LongTermRecallMemoryHarness(runtime=self),
            MemoryBootstrapHarness(runtime=self),
            MemoryCommitHarness(runtime=self),
        ]
