from __future__ import annotations

from typing import Any

from ..execution import ExecutionRuntime
from ..interaction import HumanInputResumeHarness
from ..kernel.harness import RuntimeHarness
from ..kernel.loop import KernelLoop
from ..kernel.microcompact import MidRunMicrocompactHarness
from ..kernel.model_io import ModelIO
from ..memory import KernelMemoryRuntime
from ..retry import RetryConfig
from ..tools import ToolExecutionHarness, ToolPromptHarness
from .workspace_artifacts import WorkspaceChangeArtifactHarness


def build_default_runtime_components() -> list[RuntimeHarness]:
    return [
        ToolPromptHarness(),
        ToolExecutionHarness(),
        MidRunMicrocompactHarness(),
        HumanInputResumeHarness(),
        WorkspaceChangeArtifactHarness(),
    ]


def attach_default_runtime_components(loop: KernelLoop) -> None:
    existing_names = {harness.name for harness in loop.harnesses}
    for component in build_default_runtime_components():
        if component.name in existing_names:
            continue
        loop.register_harness(component)
        existing_names.add(component.name)


def attach_memory_runtime_components(loop: KernelLoop, memory_runtime: KernelMemoryRuntime) -> None:
    existing_names = {harness.name for harness in loop.harnesses}
    for component in memory_runtime.build_default_components():
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
    **kwargs: Any,
) -> KernelLoop:
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
        **kwargs,
    )
    attach_default_runtime_components(loop)
    if memory_runtime is not None:
        attach_memory_runtime_components(loop, memory_runtime)
    return loop


__all__ = [
    "attach_default_runtime_components",
    "attach_memory_runtime_components",
    "build_default_runtime_components",
    "build_runtime_loop",
]
