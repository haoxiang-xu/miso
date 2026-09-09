from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from unchain.journal import EventCursor
from unchain.kernel.harness import BaseRuntimeHarness, HarnessContext, RuntimePhase

from .factory import ContextExecutionBundleError
from .graph_checkpoint import (
    GraphCheckpointService,
    GraphExecutionPlan,
    GraphStepDisposition,
    GraphStepResumeReceipt,
    GraphStepStartReceipt,
)
from .runtime import ContextRuntime


class GraphStepBootstrapError(RuntimeError):
    """A graph step cannot safely enter its provider-backed run."""


class GraphStepResumeError(GraphStepBootstrapError):
    """A graph step cannot safely resume its provider-backed run."""


@dataclass(frozen=True)
class GraphStepBootstrapBinding:
    """Host-resolved graph admission authority for one exact step attempt."""

    service: GraphCheckpointService
    plan: GraphExecutionPlan
    step_index: int

    def __post_init__(self) -> None:
        if not isinstance(self.service, GraphCheckpointService):
            raise TypeError("service must be a GraphCheckpointService")
        if not isinstance(self.plan, GraphExecutionPlan):
            raise TypeError("plan must be a GraphExecutionPlan")
        if (
            isinstance(self.step_index, bool)
            or not isinstance(self.step_index, int)
            or not 0 <= self.step_index < len(self.plan.steps)
        ):
            raise ValueError("step_index is outside the graph plan")
        if self.service.repository.journal.execution_id != self.plan.execution_id:
            raise ValueError("graph service and plan must share one execution")

    @property
    def step(self):
        return self.plan.steps[self.step_index]


class GraphStepBootstrapBindingResolver(Protocol):
    def __call__(self, context: HarnessContext) -> GraphStepBootstrapBinding:
        ...


@dataclass(frozen=True)
class GraphStepResumeBinding:
    """Host-resolved exact interaction evidence for one graph step resume."""

    service: GraphCheckpointService
    plan: GraphExecutionPlan
    step_index: int
    interaction_id: str
    request_cursor: EventCursor
    resolution_cursor: EventCursor

    def __post_init__(self) -> None:
        if not isinstance(self.service, GraphCheckpointService):
            raise TypeError("service must be a GraphCheckpointService")
        if not isinstance(self.plan, GraphExecutionPlan):
            raise TypeError("plan must be a GraphExecutionPlan")
        if (
            isinstance(self.step_index, bool)
            or not isinstance(self.step_index, int)
            or not 0 <= self.step_index < len(self.plan.steps)
        ):
            raise ValueError("step_index is outside the graph plan")
        if not isinstance(self.interaction_id, str) or not self.interaction_id.strip():
            raise ValueError("interaction_id must be non-empty text")
        object.__setattr__(self, "interaction_id", self.interaction_id.strip())
        for field_name in ("request_cursor", "resolution_cursor"):
            value = getattr(self, field_name)
            if not isinstance(value, EventCursor):
                object.__setattr__(
                    self,
                    field_name,
                    EventCursor.from_dict(value),
                )
        if self.request_cursor.store_seq >= self.resolution_cursor.store_seq:
            raise ValueError("interaction resolution must follow its request")
        if self.service.repository.journal.execution_id != self.plan.execution_id:
            raise ValueError("graph service and plan must share one execution")

    @property
    def step(self):
        return self.plan.steps[self.step_index]


class GraphStepResumeBindingResolver(Protocol):
    def __call__(self, context: HarnessContext) -> GraphStepResumeBinding:
        ...


@dataclass
class GraphStepBootstrapHarness(BaseRuntimeHarness):
    """Persist one graph step admission before any provider request."""

    name: str = "context_v2_graph_step_bootstrap"
    phases: tuple[RuntimePhase, ...] = ("bootstrap",)
    order: int = -980
    runtime: ContextRuntime = field(repr=False, kw_only=True)
    binding_resolver: GraphStepBootstrapBindingResolver = field(
        repr=False,
        kw_only=True,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, ContextRuntime):
            raise TypeError("runtime must be an official ContextRuntime")
        if not callable(self.binding_resolver):
            raise TypeError("binding_resolver must be callable")

    def build_delta(self, context: HarnessContext) -> None:
        binding = self.binding_resolver(context)
        if not isinstance(binding, GraphStepBootstrapBinding):
            raise TypeError(
                "binding_resolver must return a GraphStepBootstrapBinding"
            )
        step = binding.step
        run_id = context.event.get("run_id")
        session_id = context.state.session_state.session_id
        if run_id != step.attempt.attempt_id:
            raise GraphStepBootstrapError(
                "graph bootstrap run_id does not match the plan step attempt"
            )
        if session_id != step.attempt.generation.execution_id:
            raise GraphStepBootstrapError(
                "graph bootstrap session does not match the plan execution"
            )
        if binding.plan.execution_id != step.attempt.generation.execution_id:
            raise GraphStepBootstrapError(
                "graph plan and step escaped their shared execution"
            )
        try:
            bundle = self.runtime._bundle_for_context(context)
        except ContextExecutionBundleError as error:
            raise GraphStepBootstrapError(
                "graph bootstrap occurred before official context binding"
            ) from error
        if bundle.attempt != step.attempt:
            raise GraphStepBootstrapError(
                "official context binding does not match the graph step attempt"
            )
        if (
            bundle.journal.execution_id
            != binding.service.repository.journal.execution_id
        ):
            raise GraphStepBootstrapError(
                "graph bootstrap does not share the official context execution"
            )

        self.runtime.bind_graph_step_scope(
            context,
            workflow_node_id=step.node_id,
            workflow_step_index=step.index,
            workflow_step_count=len(binding.plan.steps),
        )

        receipt = binding.service.start_step(
            binding.plan,
            binding.step_index,
        )
        if type(receipt) is not GraphStepStartReceipt or receipt.step != step:
            raise GraphStepBootstrapError(
                "graph checkpoint returned a mismatched step admission"
            )
        if receipt.disposition is GraphStepDisposition.SKIP_COMPLETED:
            raise GraphStepBootstrapError(
                "graph step is already complete; host must recover and skip it "
                "before starting an agent run"
            )
        if receipt.disposition is not GraphStepDisposition.STARTED:
            raise GraphStepBootstrapError(
                "graph checkpoint returned an unsupported step disposition"
            )
        if (
            receipt.started_cursor is None
            or receipt.handoff is None
            or receipt.handoff.input_cursor.store_seq
            >= receipt.started_cursor.store_seq
        ):
            raise GraphStepBootstrapError(
                "graph step admission is missing its ordered derived input"
            )
        return None


@dataclass
class GraphStepResumeHarness(BaseRuntimeHarness):
    """Admit one exact resolved interaction before a resumed provider request."""

    name: str = "context_v2_graph_step_resume"
    phases: tuple[RuntimePhase, ...] = ("bootstrap",)
    order: int = -980
    runtime: ContextRuntime = field(repr=False, kw_only=True)
    binding_resolver: GraphStepResumeBindingResolver = field(
        repr=False,
        kw_only=True,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.runtime, ContextRuntime):
            raise TypeError("runtime must be an official ContextRuntime")
        if not callable(self.binding_resolver):
            raise TypeError("binding_resolver must be callable")

    def build_delta(self, context: HarnessContext) -> None:
        binding = self.binding_resolver(context)
        if not isinstance(binding, GraphStepResumeBinding):
            raise TypeError(
                "binding_resolver must return a GraphStepResumeBinding"
            )
        step = binding.step
        run_id = context.event.get("run_id")
        session_id = context.state.session_state.session_id
        if run_id != step.attempt.attempt_id:
            raise GraphStepResumeError(
                "graph resume run_id does not match the plan step attempt"
            )
        if session_id != step.attempt.generation.execution_id:
            raise GraphStepResumeError(
                "graph resume session does not match the plan execution"
            )
        if binding.plan.execution_id != step.attempt.generation.execution_id:
            raise GraphStepResumeError(
                "graph resume plan and step escaped their shared execution"
            )
        try:
            bundle = self.runtime._bundle_for_context(context)
        except ContextExecutionBundleError as error:
            raise GraphStepResumeError(
                "graph resume occurred before official context binding"
            ) from error
        if bundle.attempt != step.attempt:
            raise GraphStepResumeError(
                "official context binding does not match the graph resume attempt"
            )
        if (
            bundle.journal.execution_id
            != binding.service.repository.journal.execution_id
        ):
            raise GraphStepResumeError(
                "graph resume does not share the official context execution"
            )

        recovery = binding.service.recover(binding.plan)
        if binding.step_index < len(recovery.completed_steps):
            raise GraphStepResumeError(
                "graph step is already complete; host must recover and skip it "
                "before resuming an agent run"
            )
        if recovery.terminal_status is not None:
            raise GraphStepResumeError(
                "terminal graph execution cannot resume an agent run"
            )

        self.runtime.bind_graph_step_scope(
            context,
            workflow_node_id=step.node_id,
            workflow_step_index=step.index,
            workflow_step_count=len(binding.plan.steps),
        )
        receipt = binding.service.resume_step(
            binding.plan,
            binding.step_index,
            interaction_id=binding.interaction_id,
            request_cursor=binding.request_cursor,
            resolution_cursor=binding.resolution_cursor,
        )
        if (
            type(receipt) is not GraphStepResumeReceipt
            or receipt.step != step
            or receipt.graph_plan_id != binding.plan.plan_id
            or receipt.graph_scope_id != binding.plan.scope_id
            or receipt.interaction_id != binding.interaction_id
            or receipt.request_cursor != binding.request_cursor
            or receipt.resolution_cursor != binding.resolution_cursor
            or receipt.admitted_cursor.store_seq
            <= binding.resolution_cursor.store_seq
        ):
            raise GraphStepResumeError(
                "graph checkpoint returned a mismatched resume admission"
            )
        return None


__all__ = [
    "GraphStepBootstrapBinding",
    "GraphStepBootstrapBindingResolver",
    "GraphStepBootstrapError",
    "GraphStepBootstrapHarness",
    "GraphStepResumeBinding",
    "GraphStepResumeBindingResolver",
    "GraphStepResumeError",
    "GraphStepResumeHarness",
]
