from __future__ import annotations

from dataclasses import dataclass

from ..capabilities import RunDelta, SetRuntimeStateOp
from .base import BaseMemoryHarness, MemoryContext
from .effects import build_memory_commit_event, build_memory_prepare_event


def _reset_memory_info_delta(
    *,
    created_by: str,
    state_key: str,
    component_key: str,
    reason: str,
) -> RunDelta:
    return RunDelta(
        created_by=created_by,
        context_ops=(
            SetRuntimeStateOp(
                path=(state_key,),
                value={},
                reason=reason,
            ),
            SetRuntimeStateOp(
                path=("component_state", "memory", component_key),
                value={},
                reason=reason,
            ),
        ),
    )


@dataclass
class MemoryPrepareInfoResetHarness(BaseMemoryHarness):
    name: str = "memory_prepare_reset"
    phases: tuple[str, ...] = ("before_model",)
    order: int = 0

    def build_memory_delta(self, context: MemoryContext) -> RunDelta | None:
        if context.iteration <= 0:
            return None
        return _reset_memory_info_delta(
            created_by=self.created_by,
            state_key="memory_prepare_info",
            component_key="prepare_info",
            reason="memory.prepare.reset",
        )


@dataclass
class MemoryPrepareEventHarness(BaseMemoryHarness):
    name: str = "memory_prepare_event"
    phases: tuple[str, ...] = ("before_model",)
    order: int = 1000

    def build_memory_delta(self, context: MemoryContext) -> RunDelta | None:
        if not context.state.memory_prepare_info:
            return None
        return RunDelta(
            created_by=self.created_by,
            context_ops=(build_memory_prepare_event(context.state.memory_prepare_info),),
            trace={"memory_prepare_event": True},
        )


@dataclass
class MemoryCommitInfoResetHarness(BaseMemoryHarness):
    name: str = "memory_commit_reset"
    phases: tuple[str, ...] = ("before_commit",)
    order: int = 0

    def build_memory_delta(self, context: MemoryContext) -> RunDelta:
        return _reset_memory_info_delta(
            created_by=self.created_by,
            state_key="memory_commit_info",
            component_key="commit_info",
            reason="memory.commit.reset",
        )


@dataclass
class MemoryCommitEventHarness(BaseMemoryHarness):
    name: str = "memory_commit_event"
    phases: tuple[str, ...] = ("before_commit",)
    order: int = 1000

    def build_memory_delta(self, context: MemoryContext) -> RunDelta | None:
        if not context.state.memory_commit_info:
            return None
        return RunDelta(
            created_by=self.created_by,
            context_ops=(build_memory_commit_event(context.state.memory_commit_info),),
            trace={"memory_commit_event": True},
        )


__all__ = [
    "MemoryCommitEventHarness",
    "MemoryCommitInfoResetHarness",
    "MemoryPrepareEventHarness",
    "MemoryPrepareInfoResetHarness",
]
