from __future__ import annotations

from dataclasses import dataclass

from .base import BaseMemoryHarness, MemoryContext
from .checkpoint_state import (
    EXECUTION_CHECKPOINT_KEY,
    merge_checkpoint_transcript_with_incoming,
)
from .effects import build_memory_delta, memory_prepare_update, memory_state_update
from .ownership import ensure_no_external_provider_history


@dataclass
class MemoryBootstrapHarness(BaseMemoryHarness):
    name: str = "memory_bootstrap"
    phases: tuple[str, ...] = ("bootstrap", "on_resume")
    order: int = 10

    def build_memory_delta(self, context: MemoryContext) -> HarnessDelta | None:
        if context.phase == "on_resume" and context.state.memory_state.get("loaded"):
            return None

        resume_mode = bool(context.event.get("resume_mode", False))
        if context.phase == "bootstrap" and context.session_id and not resume_mode:
            ensure_no_external_provider_history(
                provider=context.state.provider_state.provider,
                previous_response_id=context.state.provider_state.previous_response_id,
            )
        merged_messages, loaded_state, prepare_info, summary_text = context.runtime.bootstrap_session(
            session_id=context.session_id,
            memory_namespace=context.memory_namespace,
            incoming_messages=context.state.transcript,
            resume_mode=resume_mode,
            provider=context.provider,
            model=context.model,
            resume_continuation=(
                context.event.get("continuation")
                if isinstance(context.event.get("continuation"), dict)
                else None
            ),
        )
        checkpoint_restored = bool(
            prepare_info.get("execution_checkpoint_restored", False)
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
            "execution_checkpoint_restored": checkpoint_restored,
            "execution_checkpoint_status": str(
                prepare_info.get("execution_checkpoint_status") or ""
            ),
            "execution_checkpoint_id": str(
                prepare_info.get("execution_checkpoint_id") or ""
            ),
            "session_revision": prepare_info.get("session_revision"),
            "session_revision_supported": bool(
                prepare_info.get("session_revision_supported", False)
            ),
            "session_consistency": str(
                prepare_info.get("session_consistency") or "best_effort"
            ),
        }
        state_updates = {
            "transcript": merged_messages,
            **memory_state_update(memory_state),
            **memory_prepare_update(prepare_info),
        }
        if summary_text:
            state_updates["optimizer_state"] = {
                "llm_summary": {
                    "summary": summary_text,
                }
            }
        if checkpoint_restored:
            raw_checkpoint = loaded_state.get(EXECUTION_CHECKPOINT_KEY)
            replay_frame = (
                raw_checkpoint.get("replay_frame")
                if isinstance(raw_checkpoint, dict)
                else None
            )
            if isinstance(replay_frame, dict):
                state_updates["provider_replay_frame"] = replay_frame
            if isinstance(raw_checkpoint, dict):
                pending_model_context = raw_checkpoint.get("pending_model_context")
                if isinstance(pending_model_context, list):
                    state_updates["next_model_input"] = (
                        pending_model_context
                        if resume_mode
                        else merge_checkpoint_transcript_with_incoming(
                            pending_model_context,
                            context.state.transcript,
                        )
                    )
                state_updates["iteration"] = int(
                    raw_checkpoint.get("iteration") or 0
                )
                raw_token_state = raw_checkpoint.get("token_state")
                if isinstance(raw_token_state, dict):
                    state_updates["token_state"] = raw_token_state
                raw_workspace_state = raw_checkpoint.get("workspace_change_state")
                if isinstance(raw_workspace_state, dict):
                    state_updates["workspace_change_state"] = raw_workspace_state
            state_updates["provider_state"] = {
                "previous_response_id": None,
                "use_previous_response_chain": False,
                "max_context_window_tokens": int(
                    raw_checkpoint.get("max_context_window_tokens") or 0
                )
                if isinstance(raw_checkpoint, dict)
                else 0,
            }
        return build_memory_delta(
            created_by=self.created_by,
            state_updates=state_updates,
            trace={
                "loaded_message_count": prepare_info.get("loaded_message_count", 0),
                "resume_mode": resume_mode,
            },
        )
