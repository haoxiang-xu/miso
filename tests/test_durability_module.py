from __future__ import annotations

from types import SimpleNamespace

from unchain.agent.modules import DurabilityModule
from unchain.memory import (
    InMemorySessionStore,
    KernelMemoryRuntime,
    MemoryRuntimeComponentMode,
)


def test_durability_module_attaches_explicit_non_context_runtime_mode() -> None:
    attached = []
    runtime = KernelMemoryRuntime.from_config(store=InMemorySessionStore())
    builder = SimpleNamespace(
        attach_memory_runtime=lambda value, **kwargs: attached.append(
            (value, kwargs)
        )
    )

    DurabilityModule(runtime=runtime).configure(builder)

    assert attached == [
        (
            runtime,
            {"component_mode": MemoryRuntimeComponentMode.DURABILITY_ONLY},
        )
    ]
