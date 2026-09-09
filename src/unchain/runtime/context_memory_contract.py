from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar


CONTEXT_MEMORY_CONTRACT_VERSION = 1
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_COMPONENTS = (
    "canonical_journal",
    "context_compiler",
    "artifact_handoff",
    "memory_workspace",
    "memory_toolkit",
    "memory_curator",
    "long_term_promotion",
)


def _revision(value: object) -> str:
    if not isinstance(value, str) or _REVISION_RE.fullmatch(value) is None:
        raise ValueError("revision must be a lowercase immutable commit SHA")
    return value


@dataclass(frozen=True, slots=True)
class ContextMemoryCapability:
    """Build-bound capability record consumed by product runtime handshakes."""

    SCHEMA: ClassVar[str] = "unchain.context_memory_capability.v1"

    revision: str
    context_memory_contract: int = CONTEXT_MEMORY_CONTRACT_VERSION
    components: tuple[str, ...] = _COMPONENTS

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision", _revision(self.revision))
        if (
            isinstance(self.context_memory_contract, bool)
            or self.context_memory_contract != CONTEXT_MEMORY_CONTRACT_VERSION
        ):
            raise ValueError("context memory contract version is unsupported")
        if isinstance(self.components, (str, bytes, bytearray)):
            raise TypeError("components must be a sequence")
        components = tuple(self.components)
        if components != _COMPONENTS:
            raise ValueError("components do not match the context memory contract")
        object.__setattr__(self, "components", components)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "revision": self.revision,
            "context_memory_contract": self.context_memory_contract,
            "components": list(self.components),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ContextMemoryCapability:
        if not isinstance(value, Mapping):
            raise TypeError("context memory capability must be an object")
        expected = {
            "schema",
            "revision",
            "context_memory_contract",
            "components",
        }
        if set(value) != expected or value.get("schema") != cls.SCHEMA:
            raise ValueError("context memory capability schema is invalid")
        raw_components = value["components"]
        if isinstance(raw_components, (str, bytes, bytearray)) or not isinstance(
            raw_components,
            Sequence,
        ):
            raise TypeError("components must be a sequence")
        return cls(
            revision=value["revision"],
            context_memory_contract=value["context_memory_contract"],
            components=tuple(raw_components),
        )


__all__ = (
    "CONTEXT_MEMORY_CONTRACT_VERSION",
    "ContextMemoryCapability",
)
