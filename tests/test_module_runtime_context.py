from __future__ import annotations

import pytest

from unchain.runtime.module_context import (
    AgentRuntimeContext,
    ExecutionIdentity,
    ModuleGrant,
)


def _root_identity() -> ExecutionIdentity:
    return ExecutionIdentity(
        execution_id="execution-a",
        attempt_id="attempt-a",
        run_id="run-a",
        run_lineage=("run-a",),
    )


def test_execution_identity_describes_lineage_without_a_role():
    root = _root_identity()
    child = ExecutionIdentity(
        execution_id="execution-child",
        attempt_id="attempt-child",
        run_id="run-child",
        run_lineage=(*root.run_lineage, "run-child"),
    )

    assert child.parent_run_id == root.run_id
    assert child.root_run_id == root.root_run_id
    assert not hasattr(child, "role")


def test_run_lineage_must_terminate_at_the_current_run():
    with pytest.raises(ValueError, match="terminate at run_id"):
        ExecutionIdentity(
            execution_id="execution-a",
            attempt_id="attempt-child",
            run_id="run-child",
            run_lineage=("run-root",),
        )


def test_module_grant_rejects_delegation_escalation():
    with pytest.raises(ValueError, match="capability subset"):
        ModuleGrant(
            module_key="memory_v2",
            capabilities=frozenset({"memory.workspace.read"}),
            delegable_capabilities=frozenset({"memory.workspace.write"}),
        )


def test_child_context_receives_only_delegable_capabilities_and_no_authority():
    root = _root_identity()
    context = AgentRuntimeContext(
        identity=root,
        module_grants=(
            ModuleGrant(
                module_key="memory_v2",
                capabilities=frozenset(
                    {
                        "memory.workspace.read",
                        "memory.candidate.propose",
                        "memory.execution.complete",
                    }
                ),
                delegable_capabilities=frozenset(
                    {
                        "memory.workspace.read",
                        "memory.candidate.propose",
                    }
                ),
                authority="completion-authority-a",
            ),
        ),
    )
    child_identity = ExecutionIdentity(
        execution_id="execution-child",
        attempt_id="attempt-child",
        run_id="run-child",
        run_lineage=(*root.run_lineage, "run-child"),
    )

    child = context.delegated_to(child_identity)

    grant = child.grant_for("memory_v2")
    assert grant is not None
    assert grant.capabilities == frozenset(
        {"memory.workspace.read", "memory.candidate.propose"}
    )
    assert grant.authority is None


def test_child_context_must_preserve_parent_and_root_lineage():
    context = AgentRuntimeContext(identity=_root_identity())
    wrong_parent = ExecutionIdentity(
        execution_id="execution-child",
        attempt_id="attempt-child",
        run_id="run-child",
        run_lineage=("run-other-root", "run-other-parent", "run-child"),
    )

    with pytest.raises(ValueError, match="extend the current run lineage"):
        context.delegated_to(wrong_parent)


def test_child_requested_capabilities_cannot_exceed_parent_delegation():
    root = _root_identity()
    context = AgentRuntimeContext(
        identity=root,
        module_grants=(
            ModuleGrant(
                module_key="example",
                capabilities=frozenset({"read", "write"}),
                delegable_capabilities=frozenset({"read"}),
            ),
        ),
    )
    child_identity = ExecutionIdentity(
        execution_id="execution-child",
        attempt_id="attempt-child",
        run_id="run-child",
        run_lineage=(*root.run_lineage, "run-child"),
    )

    with pytest.raises(ValueError, match="exceed delegation"):
        context.delegated_to(
            child_identity,
            requested_capabilities={"example": frozenset({"write"})},
        )


def test_child_can_request_a_smaller_per_module_capability_set():
    root = _root_identity()
    context = AgentRuntimeContext(
        identity=root,
        module_grants=(
            ModuleGrant(
                module_key="example",
                capabilities=frozenset({"read", "propose"}),
                delegable_capabilities=frozenset({"read", "propose"}),
            ),
        ),
    )
    child_identity = ExecutionIdentity(
        execution_id="execution-child",
        attempt_id="attempt-child",
        run_id="run-child",
        run_lineage=(*root.run_lineage, "run-child"),
    )

    child = context.delegated_to(
        child_identity,
        requested_capabilities={"example": frozenset({"read"})},
    )

    assert child.grant_for("example").capabilities == frozenset({"read"})
