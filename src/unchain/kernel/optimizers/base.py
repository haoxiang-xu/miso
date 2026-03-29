from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ...tools.toolkit import Toolkit
from ..delta import HarnessDelta, ReplaceSpanOp
from ..harness import BaseRuntimeHarness, HarnessContext, RuntimeHarness, RuntimePhase
from ..state import RunState


@dataclass(frozen=True)
class OptimizerContext:
    harness_context: HarnessContext
    optimizer_name: str

    @property
    def state(self) -> RunState:
        return self.harness_context.state

    @property
    def phase(self) -> RuntimePhase:
        return self.harness_context.phase

    @property
    def event(self) -> dict[str, Any]:
        return self.harness_context.event_payload()

    @property
    def latest_version_id(self) -> str | None:
        return self.harness_context.latest_version_id

    @property
    def provider(self) -> str:
        return str(self.state.provider_state.provider or "")

    @property
    def model(self) -> str:
        return str(self.state.provider_state.model or "")

    @property
    def session_id(self) -> str:
        return str(self.state.session_state.session_id or "")

    @property
    def memory_namespace(self) -> str:
        return str(self.state.session_state.memory_namespace or "")

    @property
    def max_context_window_tokens(self) -> int:
        return max(0, int(self.state.provider_state.max_context_window_tokens or 0))

    @property
    def toolkit(self) -> Toolkit | None:
        toolkit = self.event.get("toolkit")
        return toolkit if isinstance(toolkit, Toolkit) else None

    def latest_messages(self) -> list[dict[str, Any]]:
        return self.harness_context.latest_messages()

    def view_messages(self, version_id: str | None = None) -> list[dict[str, Any]]:
        return self.harness_context.view_messages(version_id)

    def optimizer_state(self) -> dict[str, Any]:
        bucket = self.state.optimizer_state.get(self.optimizer_name, {})
        return copy.deepcopy(bucket if isinstance(bucket, dict) else {})

    def resolve_tool(self, tool_name: str) -> Any | None:
        toolkit = self.toolkit
        if toolkit is None:
            return None
        return toolkit.get(tool_name)


@runtime_checkable
class ContextOptimizer(RuntimeHarness, Protocol):
    ...


@dataclass
class BaseContextOptimizer(BaseRuntimeHarness):
    phases: tuple[RuntimePhase, ...] = ("before_model",)

    @property
    def created_by(self) -> str:
        return f"optimizer.{self.name}"

    def build_delta(self, context: HarnessContext) -> HarnessDelta | None:
        optimizer_context = OptimizerContext(harness_context=context, optimizer_name=self.name)
        return self.build_optimizer_delta(optimizer_context)

    def build_optimizer_delta(self, context: OptimizerContext) -> HarnessDelta | None:
        raise NotImplementedError

    def build_state_updates(self, bucket: dict[str, Any] | None) -> dict[str, Any]:
        if bucket is None:
            return {}
        return {
            "optimizer_state": {
                self.name: copy.deepcopy(bucket),
            }
        }

    def state_only_delta(
        self,
        *,
        bucket: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> HarnessDelta | None:
        state_updates = self.build_state_updates(bucket)
        if not state_updates and not trace:
            return None
        return HarnessDelta(
            created_by=self.created_by,
            state_updates=state_updates,
            trace=copy.deepcopy(trace) if isinstance(trace, dict) else {},
        )

    def replace_messages_delta(
        self,
        context: OptimizerContext,
        messages: list[dict[str, Any]],
        *,
        bucket: dict[str, Any] | None = None,
        trace: dict[str, Any] | None = None,
    ) -> HarnessDelta | None:
        current_messages = context.latest_messages()
        if current_messages == messages:
            return self.state_only_delta(bucket=bucket, trace=trace)
        return HarnessDelta(
            created_by=self.created_by,
            base_version_id=context.latest_version_id,
            ops=(
                ReplaceSpanOp(
                    start=0,
                    end=len(current_messages),
                    messages=copy.deepcopy(messages),
                ),
            ),
            state_updates=self.build_state_updates(bucket),
            trace=copy.deepcopy(trace) if isinstance(trace, dict) else {},
        )
