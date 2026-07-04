from __future__ import annotations

from typing import Any

from ..interaction import HumanInputResumeHarness
from ..kernel.harness import RuntimeHarness
from ..kernel.loop import KernelLoop
from ..kernel.model_io import ModelIO
from ..memory import KernelMemoryRuntime
from ..retry import RetryConfig
from ..tools import ToolExecutionHarness, ToolPromptHarness
from .workspace_artifacts import WorkspaceChangeArtifactHarness


def build_default_runtime_components() -> list[RuntimeHarness]:
    return [
        ToolPromptHarness(),
        ToolExecutionHarness(),
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
    retry_config: RetryConfig | None = None,
    **kwargs: Any,
) -> KernelLoop:
    loop = KernelLoop(
        harnesses=harnesses,
        model_io=model_io,
        retry_config=retry_config,
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
