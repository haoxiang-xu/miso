from __future__ import annotations

from dataclasses import dataclass

from ..kernel.delta import HarnessDelta
from .base import BaseMemoryHarness, MemoryContext


@dataclass
class MemoryCommitHarness(BaseMemoryHarness):
    name: str = "memory_commit"
    phases: tuple[str, ...] = ("before_commit",)
    order: int = 10

    def build_memory_delta(self, context: MemoryContext) -> HarnessDelta | None:
        summary_bucket = context.state.optimizer_state.get("llm_summary", {})
        summary_text = ""
        if isinstance(summary_bucket, dict):
            summary_text = str(summary_bucket.get("summary", "") or "").strip()
        commit_info, stored_state = context.runtime.commit_transcript(
            session_id=context.session_id,
            transcript=context.state.transcript,
            memory_namespace=context.memory_namespace,
            model=context.model,
            summary_text=summary_text,
        )
        memory_state = {
            "loaded": bool(context.session_id),
            "resume_mode": False,
            "session_id": context.session_id,
            "memory_namespace": commit_info.get("memory_namespace", context.memory_namespace),
            "session_snapshot": stored_state,
            "vector_indexed_until": int(stored_state.get("vector_indexed_until") or 0),
            "long_term_indexed_until": int(stored_state.get("long_term_indexed_until") or 0),
            "long_term_pending_turn_count": int(stored_state.get("long_term_pending_turn_count") or 0),
            "summary": str(stored_state.get("summary", "") or ""),
        }
        return HarnessDelta(
            created_by=self.created_by,
            state_updates={
                "memory_state": memory_state,
                "memory_commit_info": commit_info,
            },
            trace={
                "stored_message_count": int(commit_info.get("stored_message_count") or 0),
                "summary_persisted": bool(commit_info.get("summary_persisted")),
            },
        )
