"""Synchronous callback adapters for durable interaction boundaries.

Callbacks remain a convenience surface.  The durable protocol is still:
checkpoint + request, then receipt, then resume.  Keeping the adapter here
prevents the kernel loop from owning max-budget request construction or
receipt persistence details.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Callable

from ..execution import ExecutionGuard
from ..kernel.lifecycle_events import build_max_iterations_decision_payload
from ..kernel.state import RunState
from .durable import InteractionNotPendingError
from .effects import (
    build_max_budget_continuation,
    build_max_budget_suspend_request,
)
from .requests import build_max_budget_interaction_request
from .runtime import (
    DurableInteractionRuntime,
    require_unapplied_callback_receipt,
)


@dataclass
class DurableMaxBudgetCallbackAdapter:
    state: RunState
    interaction_runtime: DurableInteractionRuntime
    callback_adapter: Callable[[Any], Any]
    payload: dict[str, Any]
    response_format: Any
    effective_max: int
    run_id: str
    event_callback: Any
    emit_event: Callable[..., None]
    dispatch_on_suspend: Callable[..., None]
    current_session_revision: Callable[[], int | None]
    tool_exposure_plan_factory: Callable[[], dict[str, Any] | None] | None = None
    execution_guard: ExecutionGuard | None = None
    interaction_request: dict[str, Any] | None = field(
        init=False,
        default=None,
    )
    wait_revision: int | None = field(init=False, default=None)

    def before_wait(self) -> None:
        self.state.run_status = "max_iterations"
        decision_payload = build_max_iterations_decision_payload(
            self.state,
            max_iterations=self.effective_max,
        )
        request = build_max_budget_interaction_request(
            state=self.state,
            run_id=self.run_id,
            effective_max=self.effective_max,
            decision_payload=decision_payload,
        )
        continuation = build_max_budget_continuation(
            interaction_request=request,
            payload=copy.deepcopy(self.payload),
            response_format=self.response_format,
            effective_max=self.effective_max,
            state=self.state,
            run_id=self.run_id,
            tool_exposure_plan=(
                self.tool_exposure_plan_factory()
                if callable(self.tool_exposure_plan_factory)
                else None
            ),
        )
        suspend_op = build_max_budget_suspend_request(
            interaction_request=request,
            continuation=continuation,
        )
        self.state.last_continuation = copy.deepcopy(continuation)
        self.state.suspend_state.signal_kind = suspend_op.kind
        self.state.suspend_state.payload = copy.deepcopy(suspend_op.payload)
        self.interaction_request = request.to_dict()

        self.dispatch_on_suspend(
            self.state,
            callback=self.event_callback,
            run_id=self.run_id,
            iteration=int(self.state.iteration),
            status="max_iterations",
        )
        self.wait_revision = self.current_session_revision()
        if self.execution_guard is not None and self.wait_revision is not None:
            self.execution_guard.release_for_wait()
        self.emit_event(
            self.event_callback,
            "interaction_requested",
            self.run_id,
            iteration=int(self.state.iteration),
            interaction_request=copy.deepcopy(self.interaction_request),
        )

    def invoke(self, decision: Any) -> dict[str, Any] | None:
        response = self.callback_adapter(decision)
        if not isinstance(self.interaction_request, dict):
            raise InteractionNotPendingError(
                "max-budget request was not durably suspended"
            )
        durable_snapshot = require_unapplied_callback_receipt(
            self.interaction_runtime.record_receipt(
                str(self.state.session_state.session_id or ""),
                interaction_id=str(
                    self.interaction_request.get("interaction_id") or ""
                ),
                response=response,
                submitted_by="callback:on_max_iterations",
                expected_revision=self.wait_revision,
            )
        )
        persisted_revision = durable_snapshot.session_snapshot.revision
        self.state.memory_state.update(
            {
                "session_revision": persisted_revision,
                "session_snapshot": copy.deepcopy(
                    durable_snapshot.session_snapshot.state
                ),
            }
        )
        self.state.component_bucket("memory")["state"] = copy.deepcopy(
            self.state.memory_state
        )
        if self.execution_guard is not None:
            self.execution_guard.reacquire(
                expected_revision=persisted_revision,
            )
        return durable_snapshot.response


__all__ = ["DurableMaxBudgetCallbackAdapter"]
