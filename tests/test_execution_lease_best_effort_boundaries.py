from __future__ import annotations

from typing import Any

import pytest

from unchain.execution import ActiveExecutionLeaseError
from unchain.tools import (
    ToolExposureRuntime,
    ToolObservationRunner,
    ToolOptimizerConfig,
    Toolkit,
)


class _LeaseRejectingModelIO:
    provider = "openai"
    model = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def fetch_turn(self, request: Any) -> Any:
        del request
        self.calls += 1
        raise ActiveExecutionLeaseError(
            "model boundary no longer owns the execution",
            execution_id="lease-boundary-session",
            owner_id="stale-owner",
            fencing_token=1,
        )


def test_observation_does_not_swallow_active_execution_lease_error() -> None:
    model_io = _LeaseRejectingModelIO()

    with pytest.raises(ActiveExecutionLeaseError):
        ToolObservationRunner(model_io=model_io).observe_tool_batch(
            full_messages=[{"role": "user", "content": "inspect the result"}],
            tool_messages=[{"role": "tool", "content": '{"ok":true}'}],
            payload={},
            provider="openai",
        )

    assert model_io.calls == 1

def test_tool_selector_does_not_swallow_active_execution_lease_error() -> None:
    toolkit = Toolkit()
    for index in range(2):
        toolkit.register(
            lambda: {"ok": True},
            name=f"tool_{index}",
            parameters=[],
        )
    model_io = _LeaseRejectingModelIO()
    runtime = ToolExposureRuntime(
        config=ToolOptimizerConfig(max_direct_tools=1, trigger_tool_count=1),
        full_toolkit=toolkit,
        model_io=model_io,
        provider="openai",
        model="fake",
        messages=[{"role": "user", "content": "use a tool"}],
    )

    with pytest.raises(ActiveExecutionLeaseError):
        runtime.prepare()

    assert model_io.calls == 1
