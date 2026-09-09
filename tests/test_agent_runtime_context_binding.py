from __future__ import annotations

import pytest

from unchain.agent.builder import AgentCallContext
from unchain.runtime.module_context import AgentRuntimeContext, ExecutionIdentity


def _runtime_context() -> AgentRuntimeContext:
    return AgentRuntimeContext(
        identity=ExecutionIdentity(
            execution_id="execution-a",
            attempt_id="attempt-a",
            run_id="run-a",
            run_lineage=("run-a",),
        )
    )


def test_runtime_context_is_the_single_source_for_missing_call_identity():
    call = AgentCallContext(mode="run", runtime_context=_runtime_context())

    assert call.session_id == "execution-a"
    assert call.run_id == "run-a"
    assert call.execution_owner_id == "attempt-a"


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("session_id", "other-execution"),
        ("run_id", "other-run"),
        ("execution_owner_id", "other-attempt"),
    ),
)
def test_runtime_context_rejects_conflicting_legacy_identity_fields(
    field_name,
    value,
):
    values = {field_name: value}
    with pytest.raises(ValueError, match=field_name):
        AgentCallContext(
            mode="run",
            runtime_context=_runtime_context(),
            **values,
        )
