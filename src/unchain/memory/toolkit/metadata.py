from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MemoryToolkitPackageMetadata:
    package_id: str = "memory_v2_system"
    name: str = "Memory V2 System Toolkit"
    version: str = "0.1.0"
    contract_version: str = "unchain.memory_toolkit.v1"
    public_registry: bool = False
    roles: tuple[str, ...] = (
        "normal",
        "curator",
        "consolidation_curator",
        "task_state_curator",
    )


MEMORY_TOOLKIT_METADATA = MemoryToolkitPackageMetadata()


__all__ = ["MEMORY_TOOLKIT_METADATA", "MemoryToolkitPackageMetadata"]
