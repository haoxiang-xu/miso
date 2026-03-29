from __future__ import annotations

import json
from dataclasses import dataclass

from ..delta import HarnessDelta, ReplaceSpanOp
from ..optimizers.common import latest_user_query, replace_non_system_span, split_system_and_non_system
from .base import BaseMemoryHarness, MemoryContext


@dataclass
class LongTermRecallMemoryHarness(BaseMemoryHarness):
    name: str = "memory_long_term_recall"
    phases: tuple[str, ...] = ("before_model",)
    order: int = 36

    def build_memory_delta(self, context: MemoryContext) -> HarnessDelta | None:
        prepare_info: dict[str, object] = {}
        if not context.session_id:
            prepare_info["long_term_skip_reason"] = "missing_session_id"
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={"memory_prepare_info": prepare_info},
            )

        query = latest_user_query(context.latest_messages())
        long_term = context.runtime.recall_long_term(
            session_id=context.session_id,
            memory_namespace=context.memory_namespace,
            query=query,
        )
        facts = list(long_term.get("facts", [])) if isinstance(long_term, dict) else []
        episodes = list(long_term.get("episodes", [])) if isinstance(long_term, dict) else []
        playbooks = list(long_term.get("playbooks", [])) if isinstance(long_term, dict) else []
        fallback_reasons = long_term.get("fallback_reasons", {}) if isinstance(long_term, dict) else {}
        profile_result = context.runtime.recall_profile(
            session_id=context.session_id,
            memory_namespace=context.memory_namespace,
        )
        profile = profile_result.get("profile", {}) if isinstance(profile_result, dict) else {}
        profile_fallback_reason = str(profile_result.get("fallback_reason") or "").strip() if isinstance(profile_result, dict) else ""

        prepare_info.update(
            {
                "long_term_query": query,
                "long_term_fact_recall_count": len(facts),
                "long_term_episode_recall_count": len(episodes),
                "long_term_playbook_recall_count": len(playbooks),
                "long_term_profile_key_count": len(profile) if isinstance(profile, dict) else 0,
            }
        )
        if profile_fallback_reason:
            prepare_info["long_term_profile_fallback_reason"] = profile_fallback_reason
        if isinstance(fallback_reasons, dict) and fallback_reasons:
            for key, value in fallback_reasons.items():
                if value:
                    prepare_info[f"long_term_{key}_fallback_reason"] = value

        injected: list[dict[str, str]] = []
        if not context.supports_tools and isinstance(profile, dict) and profile:
            injected.append({
                "role": "system",
                "content": f"[MEMORY_PROFILE]\n{json.dumps(profile, ensure_ascii=False)}",
            })
            prepare_info["long_term_profile_applied"] = True
        if facts:
            injected.append({
                "role": "system",
                "content": f"[MEMORY_FACTS]\n{json.dumps(facts, ensure_ascii=False)}",
            })
        if episodes:
            injected.append({
                "role": "system",
                "content": f"[MEMORY_EPISODES]\n{json.dumps(episodes, ensure_ascii=False)}",
            })
        if playbooks:
            injected.append({
                "role": "system",
                "content": f"[MEMORY_PLAYBOOKS]\n{json.dumps(playbooks, ensure_ascii=False)}",
            })

        if not injected:
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={"memory_prepare_info": prepare_info},
            )

        messages = context.latest_messages()
        _, non_system = split_system_and_non_system(messages)
        updated_messages = replace_non_system_span(
            messages,
            non_system,
            injected_system_messages=injected,
        )
        return HarnessDelta(
            created_by=self.created_by,
            base_version_id=context.latest_version_id,
            ops=(
                ReplaceSpanOp(
                    start=0,
                    end=len(messages),
                    messages=updated_messages,
                ),
            ),
            state_updates={"memory_prepare_info": prepare_info},
            trace={"long_term_injected_count": len(injected)},
        )
