from __future__ import annotations

from dataclasses import dataclass

from ..kernel.delta import HarnessDelta
from .base import BaseMemoryHarness, MemoryContext


@dataclass
class MemoryBootstrapHarness(BaseMemoryHarness):
    name: str = "memory_bootstrap"
    phases: tuple[str, ...] = ("bootstrap", "on_resume")
    order: int = 10

    def build_memory_delta(self, context: MemoryContext) -> HarnessDelta | None:
        if context.phase == "on_resume" and context.state.memory_state.get("loaded"):
            return None

        resume_mode = bool(context.event.get("resume_mode", False))
        merged_messages, loaded_state, prepare_info, summary_text = context.runtime.bootstrap_session(
            session_id=context.session_id,
            memory_namespace=context.memory_namespace,
            incoming_messages=context.state.transcript,
            resume_mode=resume_mode,
        )
        memory_state = {
            "loaded": bool(context.session_id),
            "resume_mode": resume_mode,
            "session_id": context.session_id,
            "memory_namespace": prepare_info.get("memory_namespace", context.memory_namespace),
            "session_snapshot": loaded_state,
            "vector_indexed_until": int(loaded_state.get("vector_indexed_until") or 0),
            "long_term_indexed_until": int(loaded_state.get("long_term_indexed_until") or 0),
            "long_term_pending_turn_count": int(loaded_state.get("long_term_pending_turn_count") or 0),
            "summary": summary_text,
        }
        state_updates = {
            "transcript": merged_messages,
            "memory_state": memory_state,
            "memory_prepare_info": prepare_info,
        }
        if summary_text:
            state_updates["optimizer_state"] = {
                "llm_summary": {
                    "summary": summary_text,
                }
            }
        return HarnessDelta(
            created_by=self.created_by,
            state_updates=state_updates,
            trace={
                "loaded_message_count": prepare_info.get("loaded_message_count", 0),
                "resume_mode": resume_mode,
            },
        )
