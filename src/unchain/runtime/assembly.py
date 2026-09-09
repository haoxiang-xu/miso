from __future__ import annotations

from typing import Any

from ..execution import ExecutionRuntime
from ..interaction import HumanInputResumeHarness
from ..interaction.runtime import DurableInteractionRuntime
from ..kernel.harness import RuntimeHarness
from ..kernel.loop import KernelLoop
from ..kernel.microcompact import MidRunMicrocompactHarness
from ..kernel.model_io import ModelIO
from ..memory import (
    KernelMemoryRuntime,
    MemoryRuntimeComponentMode,
    build_durability_memory_components,
)
from ..retry import RetryConfig
from ..tools import ToolExecutionHarness, ToolPromptHarness
from .workspace_artifacts import WorkspaceChangeArtifactHarness


def _normalized_semantic_context_owner(owner: str | None) -> str | None:
    if owner is None:
        return None
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("semantic_context_owner must be a non-empty identifier")
    return owner.strip()


def _is_incompatible_semantic_context_component(component: object) -> bool:
    from ..memory.recall_long_term import LongTermRecallMemoryHarness
    from ..memory.short_term import ShortTermRecallMemoryHarness
    from ..optimizers import (
        LastNOptimizer,
        LlmSummaryOptimizer,
        SlidingWindowOptimizer,
        ToolHistoryCompactionOptimizer,
    )
    from ..optimizers.base import BaseContextOptimizer

    known_types = (
        MidRunMicrocompactHarness,
        BaseContextOptimizer,
        ToolHistoryCompactionOptimizer,
        LlmSummaryOptimizer,
        LastNOptimizer,
        SlidingWindowOptimizer,
        ShortTermRecallMemoryHarness,
        LongTermRecallMemoryHarness,
    )
    if isinstance(component, known_types):
        return True
    capability = getattr(component, "semantic_context_capability", None)
    return capability in {
        "destructive",
        "destructive_optimizer",
        "legacy_recall",
    }


def _validate_semantic_context_assembly(
    harnesses: list[RuntimeHarness] | None,
    *,
    semantic_context_owner: str | None,
) -> str | None:
    from ..context.harness import ContextCompilerHarness

    owner = _normalized_semantic_context_owner(semantic_context_owner)
    supplied = list(harnesses or ())
    compiler_harnesses = [
        component
        for component in supplied
        if isinstance(component, ContextCompilerHarness)
    ]
    declared_owners = [
        (component, getattr(component, "semantic_context_owner", None))
        for component in supplied
        if getattr(component, "semantic_context_owner", None) is not None
    ]
    if owner is None:
        if declared_owners:
            raise ValueError(
                "a context compiler harness requires an explicit semantic_context_owner"
            )
        return None
    if len(declared_owners) != 1 or len(compiler_harnesses) != 1:
        raise ValueError(
            "an explicit semantic context owner requires exactly one compiler harness"
        )
    component, declared_owner = declared_owners[0]
    if component is not compiler_harnesses[0]:
        raise ValueError(
            "an explicit semantic context owner requires exactly one compiler harness"
        )
    if declared_owner != owner:
        raise ValueError(
            "semantic context owner does not match its compiler harness: "
            f"{owner!r} != {declared_owner!r}"
        )
    for component in supplied:
        if _is_incompatible_semantic_context_component(component):
            raise ValueError(
                f"incompatible {type(component).__name__} supplied with "
                f"semantic context owner {owner!r}"
            )
    return owner


def build_default_runtime_components(
    *,
    semantic_context_owner: str | None = None,
) -> list[RuntimeHarness]:
    owner = _normalized_semantic_context_owner(semantic_context_owner)
    if owner is None:
        return [
            ToolPromptHarness(),
            ToolExecutionHarness(),
            MidRunMicrocompactHarness(),
            HumanInputResumeHarness(),
            WorkspaceChangeArtifactHarness(),
        ]
    return [
        ToolPromptHarness(),
        ToolExecutionHarness(),
        HumanInputResumeHarness(),
        WorkspaceChangeArtifactHarness(),
    ]


def attach_default_runtime_components(
    loop: KernelLoop,
    *,
    semantic_context_owner: str | None = None,
) -> None:
    existing_names = {harness.name for harness in loop.harnesses}
    for component in build_default_runtime_components(
        semantic_context_owner=semantic_context_owner
    ):
        if component.name in existing_names:
            continue
        loop.register_harness(component)
        existing_names.add(component.name)


def attach_memory_runtime_components(
    loop: KernelLoop,
    memory_runtime: KernelMemoryRuntime,
    *,
    semantic_context_owner: str | None = None,
    component_mode: MemoryRuntimeComponentMode = MemoryRuntimeComponentMode.FULL,
) -> None:
    if not isinstance(component_mode, MemoryRuntimeComponentMode):
        raise TypeError("component_mode must be a MemoryRuntimeComponentMode")
    if loop.interaction_runtime is None:
        loop.interaction_runtime = DurableInteractionRuntime(memory_runtime)
    existing_names = {harness.name for harness in loop.harnesses}
    if component_mode is MemoryRuntimeComponentMode.DURABILITY_ONLY:
        components = build_durability_memory_components(memory_runtime)
    elif semantic_context_owner is None:
        components = memory_runtime.build_default_components()
    else:
        from ..memory.assembly import build_default_memory_components

        components = build_default_memory_components(
            memory_runtime,
            semantic_context_owner=semantic_context_owner,
        )
    for component in components:
        if component.name in existing_names:
            continue
        loop.register_harness(component)
        existing_names.add(component.name)


def build_runtime_loop(
    *,
    harnesses: list[RuntimeHarness] | None = None,
    model_io: ModelIO | None = None,
    memory_runtime: KernelMemoryRuntime | None = None,
    execution_runtime: ExecutionRuntime | None = None,
    retry_config: RetryConfig | None = None,
    semantic_context_owner: str | None = None,
    memory_runtime_component_mode: MemoryRuntimeComponentMode = (
        MemoryRuntimeComponentMode.FULL
    ),
    **kwargs: Any,
) -> KernelLoop:
    if not isinstance(memory_runtime_component_mode, MemoryRuntimeComponentMode):
        raise TypeError(
            "memory_runtime_component_mode must be a MemoryRuntimeComponentMode"
        )
    if (
        memory_runtime is None
        and memory_runtime_component_mode
        is not MemoryRuntimeComponentMode.FULL
    ):
        raise ValueError("a non-full memory mode requires a memory runtime")
    semantic_context_owner = _validate_semantic_context_assembly(
        harnesses,
        semantic_context_owner=semantic_context_owner,
    )
    if memory_runtime is not None:
        store = memory_runtime.store
        lease_methods = (
            "acquire_lease",
            "verify_lease",
            "renew_lease",
            "release_lease",
            "save_if_revision_and_fence",
        )
        lease_capability = {
            name: callable(getattr(store, name, None))
            for name in lease_methods
        }
        if any(lease_capability.values()) and not all(lease_capability.values()):
            missing = ", ".join(
                name for name, present in lease_capability.items() if not present
            )
            raise TypeError(
                "memory session store execution fencing capability is incomplete: "
                + missing
            )
        if all(lease_capability.values()):
            revision_methods = ("load_with_revision", "save_if_revision")
            missing_revision_methods = [
                name
                for name in revision_methods
                if not callable(getattr(store, name, None))
            ]
            if missing_revision_methods:
                raise TypeError(
                    "memory session store execution fencing requires revisioned "
                    "load/CAS support; missing callable "
                    + ", ".join(missing_revision_methods)
                )
            if execution_runtime is None:
                execution_runtime = ExecutionRuntime(store)
        if (
            execution_runtime is not None
            and execution_runtime.store is not store
        ):
            raise ValueError(
                "execution_runtime and memory_runtime must share the same session store"
            )
    loop = KernelLoop(
        harnesses=harnesses,
        model_io=model_io,
        retry_config=retry_config,
        execution_runtime=execution_runtime,
        interaction_runtime=(
            DurableInteractionRuntime(memory_runtime)
            if memory_runtime is not None
            else None
        ),
        **kwargs,
    )
    loop._semantic_context_owner = semantic_context_owner
    attach_default_runtime_components(
        loop,
        semantic_context_owner=semantic_context_owner,
    )
    if memory_runtime is not None:
        attach_memory_runtime_components(
            loop,
            memory_runtime,
            semantic_context_owner=semantic_context_owner,
            component_mode=memory_runtime_component_mode,
        )
    return loop


__all__ = [
    "attach_default_runtime_components",
    "attach_memory_runtime_components",
    "build_default_runtime_components",
    "build_runtime_loop",
]
