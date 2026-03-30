from __future__ import annotations

from dataclasses import dataclass, field

from ..memory import InMemorySessionStore, SessionStore
from ..workspace.pins import MAX_PINNED_INJECTION_CHARS, build_pinned_prompt_messages
from .base import BaseContextOptimizer, OptimizerContext
from .common import replace_non_system_span, split_system_and_non_system


@dataclass(frozen=True)
class WorkspacePinsOptimizerConfig:
    store: SessionStore = field(default_factory=InMemorySessionStore)
    max_total_chars: int = MAX_PINNED_INJECTION_CHARS


class WorkspacePinsOptimizer(BaseContextOptimizer):
    def __init__(
        self,
        config: WorkspacePinsOptimizerConfig | None = None,
        *,
        phases=("before_model",),
        order: int = 40,
    ) -> None:
        super().__init__(name="workspace_pins", phases=phases, order=order)
        self.config = config or WorkspacePinsOptimizerConfig()

    def build_optimizer_delta(self, context: OptimizerContext):
        bucket = context.optimizer_state()
        session_id = context.session_id
        if not session_id:
            bucket["applied"] = False
            bucket["skip_reason"] = "missing_session_id"
            return self.state_only_delta(bucket=bucket)

        pin_messages = build_pinned_prompt_messages(
            store=self.config.store,
            session_id=session_id,
            max_total_chars=max(0, int(self.config.max_total_chars)),
        )
        if not pin_messages:
            bucket["applied"] = False
            bucket["skip_reason"] = "no_pins"
            bucket["injected_message_count"] = 0
            return self.state_only_delta(bucket=bucket)

        messages = context.latest_messages()
        _, non_system = split_system_and_non_system(messages)
        updated_messages = replace_non_system_span(
            messages,
            non_system,
            injected_system_messages=pin_messages,
        )
        bucket["applied"] = True
        bucket["skip_reason"] = ""
        bucket["injected_message_count"] = len(pin_messages)
        return self.replace_messages_delta(
            context,
            updated_messages,
            bucket=bucket,
            trace={
                "injected_message_count": len(pin_messages),
            },
        )
