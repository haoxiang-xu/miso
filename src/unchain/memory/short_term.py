from __future__ import annotations

import json
from dataclasses import dataclass

from ..kernel.delta import HarnessDelta, ReplaceSpanOp
from ..optimizers.common import latest_user_query, replace_non_system_span, split_system_and_non_system
from .base import BaseMemoryHarness, MemoryContext


@dataclass
class ShortTermRecallMemoryHarness(BaseMemoryHarness):
    name: str = "memory_short_term_recall"
    phases: tuple[str, ...] = ("before_model",)
    order: int = 35

    def build_memory_delta(self, context: MemoryContext) -> HarnessDelta | None:
        prepare_info: dict[str, object] = {}
        if not context.session_id:
            prepare_info["short_term_skip_reason"] = "missing_session_id"
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={"memory_prepare_info": prepare_info},
            )

        query = latest_user_query(context.latest_messages())
        if not query:
            prepare_info["short_term_skip_reason"] = "missing_query"
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={"memory_prepare_info": prepare_info},
            )

        recall_result = context.runtime.recall_memory(
            session_id=context.session_id,
            memory_namespace=context.memory_namespace,
            query=query,
            include_short_term=True,
            include_long_term=False,
        )
        short_term = recall_result.get("short_term", {})
        recalled_messages = short_term.get("messages", []) if isinstance(short_term, dict) else []
        if not isinstance(recalled_messages, list):
            recalled_messages = []

        prepare_info["short_term_query"] = query
        prepare_info["short_term_available"] = bool(short_term.get("available")) if isinstance(short_term, dict) else False
        prepare_info["short_term_hit_count"] = int(short_term.get("hit_count") or 0) if isinstance(short_term, dict) else 0
        prepare_info["short_term_recall_count"] = len(recalled_messages)
        fallback_reason = str(short_term.get("fallback_reason") or "").strip() if isinstance(short_term, dict) else ""
        if fallback_reason:
            prepare_info["short_term_fallback_reason"] = fallback_reason

        if not recalled_messages:
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={"memory_prepare_info": prepare_info},
            )

        messages = context.latest_messages()
        _, non_system = split_system_and_non_system(messages)
        updated_messages = replace_non_system_span(
            messages,
            non_system,
            injected_system_messages=[
                {
                    "role": "system",
                    "content": f"[Recall messages]\n{json.dumps(recalled_messages, ensure_ascii=False)}",
                }
            ],
        )
        if updated_messages == messages:
            return HarnessDelta(
                created_by=self.created_by,
                state_updates={"memory_prepare_info": prepare_info},
                trace={"short_term_recall_count": len(recalled_messages)},
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
            trace={"short_term_recall_count": len(recalled_messages)},
        )
