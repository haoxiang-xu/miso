from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

from unchain.journal import ModelValidationError, ResourceRef
from unchain.kernel.harness import BaseRuntimeHarness, HarnessContext, RuntimePhase
from unchain.memory.workspace import TaskStateService


PinnedTaskStateBootstrapInputKind = Literal[
    "user_message",
    "interaction_resume",
]


class CurrentInputEventRefResolver(Protocol):
    """Resolve the durable journal ref for the input already bound to this attempt."""

    def __call__(self) -> ResourceRef:
        ...


@dataclass(frozen=True)
class PinnedTaskStateBootstrapBinding:
    """Host-owned, scope-bound capabilities for one bootstrap attempt."""

    task_state: TaskStateService
    objective: str
    current_input_event_ref_resolver: CurrentInputEventRefResolver
    input_kind: PinnedTaskStateBootstrapInputKind = "user_message"

    def __post_init__(self) -> None:
        if not isinstance(self.task_state, TaskStateService):
            raise TypeError("task_state must be a scope-bound TaskStateService")
        if not isinstance(self.objective, str):
            raise TypeError("objective must be text")
        if not callable(self.current_input_event_ref_resolver):
            raise TypeError("current_input_event_ref_resolver must be callable")
        if self.input_kind not in {"user_message", "interaction_resume"}:
            raise ValueError("input_kind is invalid")


class PinnedTaskStateBootstrapBindingResolver(Protocol):
    """Return the exact host binding for a kernel bootstrap context."""

    def __call__(
        self,
        context: HarnessContext,
    ) -> PinnedTaskStateBootstrapBinding:
        ...


def _operation_id(
    *,
    binding_id: str,
    objective: str,
    source_ref: ResourceRef,
) -> str:
    encoded = json.dumps(
        {
            "binding_id": binding_id,
            "objective": objective,
            "source_ref": source_ref.to_dict(),
            "version": 1,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"pinned-task-state-bootstrap-v1-{digest}"


@dataclass
class PinnedTaskStateBootstrapHarness(BaseRuntimeHarness):
    """Create the first pinned task state after durable input binding."""

    name: str = "context_v2_pinned_task_state_bootstrap"
    phases: tuple[RuntimePhase, ...] = ("bootstrap",)
    order: int = -990
    binding_resolver: PinnedTaskStateBootstrapBindingResolver = field(
        repr=False,
        kw_only=True,
    )

    def __post_init__(self) -> None:
        if not callable(self.binding_resolver):
            raise TypeError("binding_resolver must be callable")

    def build_delta(self, context: HarnessContext) -> None:
        binding = self.binding_resolver(context)
        if not isinstance(binding, PinnedTaskStateBootstrapBinding):
            raise TypeError(
                "binding_resolver must return a PinnedTaskStateBootstrapBinding"
            )
        if binding.input_kind == "interaction_resume":
            return None

        current = binding.task_state.get()
        if current is not None:
            return None

        source_ref = binding.current_input_event_ref_resolver()
        if not isinstance(source_ref, ResourceRef):
            raise TypeError(
                "current_input_event_ref_resolver must return a ResourceRef"
            )
        if source_ref.kind != "context_event" or source_ref.fragment:
            raise ModelValidationError(
                "current input provenance must be a bare context_event reference"
            )

        binding.task_state.update(
            expected_revision=None,
            patch={"objective": binding.objective},
            source_event_refs=(source_ref,),
            operation_id=_operation_id(
                binding_id=binding.task_state.binding_id,
                objective=binding.objective,
                source_ref=source_ref,
            ),
        )
        return None


__all__ = [
    "CurrentInputEventRefResolver",
    "PinnedTaskStateBootstrapBinding",
    "PinnedTaskStateBootstrapBindingResolver",
    "PinnedTaskStateBootstrapHarness",
    "PinnedTaskStateBootstrapInputKind",
]
