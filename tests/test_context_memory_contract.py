from __future__ import annotations

import pytest

from unchain.runtime.context_memory_contract import (
    CONTEXT_MEMORY_CONTRACT_VERSION,
    ContextMemoryCapability,
)


REVISION = "a" * 40


def test_context_memory_capability_is_an_exact_immutable_record() -> None:
    capability = ContextMemoryCapability(revision=REVISION)

    assert capability.to_dict() == {
        "schema": "unchain.context_memory_capability.v1",
        "revision": REVISION,
        "context_memory_contract": CONTEXT_MEMORY_CONTRACT_VERSION,
        "components": [
            "canonical_journal",
            "context_compiler",
            "artifact_handoff",
            "memory_workspace",
            "memory_toolkit",
            "memory_curator",
            "long_term_promotion",
        ],
    }
    assert ContextMemoryCapability.from_dict(capability.to_dict()) == capability


@pytest.mark.parametrize(
    "revision",
    (
        "",
        "a" * 39,
        "a" * 41,
        "A" * 40,
        "g" * 40,
        "main",
        None,
    ),
)
def test_context_memory_capability_rejects_non_immutable_revisions(revision) -> None:
    with pytest.raises((TypeError, ValueError), match="revision"):
        ContextMemoryCapability(revision=revision)


def test_context_memory_capability_rejects_contract_or_component_drift() -> None:
    raw = ContextMemoryCapability(revision=REVISION).to_dict()

    with pytest.raises(ValueError, match="contract"):
        ContextMemoryCapability.from_dict(
            {**raw, "context_memory_contract": CONTEXT_MEMORY_CONTRACT_VERSION + 1}
        )
    with pytest.raises(ValueError, match="components"):
        ContextMemoryCapability.from_dict(
            {**raw, "components": [*raw["components"], "unknown_component"]}
        )
