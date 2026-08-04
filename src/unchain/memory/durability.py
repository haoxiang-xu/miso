from __future__ import annotations

import copy
from dataclasses import dataclass

from .base import BaseMemoryHarness, MemoryContext
from .checkpoint import ExecutionCheckpointHarness
from .checkpoint_state import (
    ExecutionCheckpointResumeRequiredError,
    ensure_checkpoint_compatible,
    merge_checkpoint_transcript_with_incoming,
    restore_fresh_checkpoint_messages,
    restore_resume_checkpoint_messages,
)
from .effects import build_memory_delta, memory_state_update


@dataclass
class DurabilityBootstrapHarness(BaseMemoryHarness):
    """Restore only execution durability state, never legacy semantic memory."""

    name: str = "memory_durability_bootstrap"
    phases: tuple[str, ...] = ("bootstrap", "on_resume")
    order: int = 10

    def build_memory_delta(self, context: MemoryContext):
        if (
            context.phase == "on_resume"
            and context.state.memory_state.get("durability_checkpoint_loaded")
        ):
            return None

        resume_mode = bool(context.event.get("resume_mode", False))
        if not context.session_id:
            prepare_info = {
                "applied": False,
                "session_id": "",
                "resume_mode": resume_mode,
                "execution_checkpoint_restored": False,
                "skip_reason": "missing_session_id",
            }
            context.runtime.last_prepare_info = copy.deepcopy(prepare_info)
            return build_memory_delta(
                created_by=self.created_by,
                state_updates=memory_state_update(
                    {
                        "durability_checkpoint_loaded": True,
                        "resume_mode": resume_mode,
                        "execution_checkpoint_restored": False,
                    }
                ),
                trace={
                    **prepare_info,
                    "semantic_memory_loaded": False,
                },
            )

        checkpoint = context.runtime.load_execution_checkpoint(
            context.session_id
        )
        session_snapshot = context.runtime.load_session_snapshot(
            context.session_id
        )
        if checkpoint is None and resume_mode:
            raise ExecutionCheckpointResumeRequiredError(
                "durability-only interaction resume requires a persisted "
                "execution checkpoint"
            )

        checkpoint_restored = isinstance(checkpoint, dict)
        state_updates = memory_state_update(
            {
                "durability_checkpoint_loaded": True,
                "resume_mode": resume_mode,
                "execution_checkpoint_restored": checkpoint_restored,
                "execution_checkpoint_status": (
                    str(checkpoint.get("status") or "")
                    if checkpoint_restored
                    else ""
                ),
                "execution_checkpoint_id": (
                    str(checkpoint.get("checkpoint_id") or "")
                    if checkpoint_restored
                    else ""
                ),
                "session_revision": session_snapshot.revision,
                "session_revision_supported": (
                    session_snapshot.revision_supported
                ),
                "session_consistency": session_snapshot.consistency,
            }
        )
        if checkpoint_restored:
            ensure_checkpoint_compatible(
                checkpoint,
                session_id=context.session_id,
                provider=context.provider,
                model=context.model,
            )
            incoming = context.latest_messages()
            if resume_mode:
                restored_messages = restore_resume_checkpoint_messages(
                    checkpoint,
                    conversation=incoming,
                    continuation=(
                        context.event.get("continuation")
                        if isinstance(context.event.get("continuation"), dict)
                        else None
                    ),
                )
            else:
                checkpoint_messages = restore_fresh_checkpoint_messages(
                    checkpoint,
                    incoming_messages=incoming,
                )
                restored_messages = merge_checkpoint_transcript_with_incoming(
                    checkpoint_messages,
                    incoming,
                )
            state_updates.update(
                {
                    "transcript": restored_messages,
                    "provider_replay_frame": copy.deepcopy(
                        checkpoint["replay_frame"]
                    ),
                    "iteration": int(checkpoint.get("iteration") or 0),
                    "token_state": copy.deepcopy(
                        checkpoint.get("token_state") or {}
                    ),
                    "workspace_change_state": copy.deepcopy(
                        checkpoint.get("workspace_change_state") or {}
                    ),
                    "provider_state": {
                        "previous_response_id": None,
                        "use_previous_response_chain": False,
                        "max_context_window_tokens": int(
                            checkpoint.get("max_context_window_tokens") or 0
                        ),
                    },
                }
            )
            pending_model_context = checkpoint.get("pending_model_context")
            if isinstance(pending_model_context, list):
                state_updates["next_model_input"] = copy.deepcopy(
                    pending_model_context
                )

        prepare_info = {
            "applied": True,
            "session_id": context.session_id,
            "resume_mode": resume_mode,
            "execution_checkpoint_restored": checkpoint_restored,
            "execution_checkpoint_status": (
                str(checkpoint.get("status") or "")
                if checkpoint_restored
                else ""
            ),
            "execution_checkpoint_id": (
                str(checkpoint.get("checkpoint_id") or "")
                if checkpoint_restored
                else ""
            ),
            "session_revision": session_snapshot.revision,
            "session_revision_supported": session_snapshot.revision_supported,
            "session_consistency": session_snapshot.consistency,
        }
        context.runtime.last_prepare_info = copy.deepcopy(prepare_info)
        return build_memory_delta(
            created_by=self.created_by,
            state_updates=state_updates,
            trace={
                **prepare_info,
                "semantic_memory_loaded": False,
            },
        )


@dataclass
class DurabilityCheckpointHarness(ExecutionCheckpointHarness):
    """Persist and clear execution checkpoints without semantic memory writes."""

    name: str = "memory_durability_checkpoint"

    def _complete(self, context):
        if not context.session_id:
            return build_memory_delta(
                created_by=self.created_by,
                trace={
                    "execution_checkpoint_applied": False,
                    "execution_checkpoint_skip_reason": "missing_session_id",
                    "semantic_commit_applied": False,
                },
            )
        checkpoint = context.runtime.load_execution_checkpoint(context.session_id)
        if not isinstance(checkpoint, dict):
            return build_memory_delta(
                created_by=self.created_by,
                trace={
                    "execution_checkpoint_applied": False,
                    "execution_checkpoint_action": "absent",
                    "semantic_commit_applied": False,
                },
            )
        checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
        cleared, snapshot = context.runtime.clear_execution_checkpoint_snapshot(
            context.session_id,
            expected_checkpoint_id=checkpoint_id or None,
            execution_fence=context.execution_fence,
        )
        return build_memory_delta(
            created_by=self.created_by,
            state_updates=memory_state_update(
                {
                    "execution_checkpoint_restored": False,
                    "execution_checkpoint_status": "",
                    "execution_checkpoint_id": "",
                    "session_revision": snapshot.revision,
                    "session_revision_supported": snapshot.revision_supported,
                    "session_consistency": snapshot.consistency,
                }
            ),
            trace={
                "execution_checkpoint_applied": True,
                "execution_checkpoint_action": (
                    "cleared" if cleared else "absent"
                ),
                "execution_checkpoint_cleared": cleared,
                "semantic_commit_applied": False,
            },
        )


__all__ = ["DurabilityBootstrapHarness", "DurabilityCheckpointHarness"]
