from __future__ import annotations

from typing import Any

import pytest

from unchain.execution import ActiveExecutionLeaseError, ExecutionFence
from unchain.kernel import KernelLoop
from unchain.memory import (
    InMemorySessionStore,
    KernelMemoryRuntime,
    LongTermMemoryConfig,
    MemoryConfig,
    MemoryManager,
)
from unchain.optimizers import LlmSummaryOptimizer, LlmSummaryOptimizerConfig
from unchain.tools import Toolkit
from unchain.tools.messages import get_provider_message_builder
from unchain.tools.models import ToolHistoryOptimizationContext
from unchain.tools.result_budget import (
    ToolResultBudgetConfig,
    ToolResultBudgetController,
)
from unchain.kernel.types import ToolCall


def _lease_error() -> ActiveExecutionLeaseError:
    return ActiveExecutionLeaseError(
        "best-effort boundary attempted an unfenced operation",
        execution_id="fatal-boundary-session",
        owner_id="owner-a",
        fencing_token=1,
    )


class _LeaseRejectingVectorAdapter:
    def similarity_search(self, **_: Any) -> list[Any]:
        raise _lease_error()

    def add_texts(self, **_: Any) -> None:
        raise _lease_error()


class _RecordingVectorAdapter:
    def __init__(self) -> None:
        self.add_calls = 0

    def similarity_search(self, **_: Any) -> list[Any]:
        return []

    def add_texts(self, **_: Any) -> None:
        self.add_calls += 1


class _NoopLongTermVectorAdapter:
    def similarity_search(self, **_: Any) -> list[Any]:
        return []

    def add_texts(self, **_: Any) -> None:
        return None


class _ProfileStore:
    def load(self, namespace: str) -> dict[str, Any]:
        del namespace
        return {}

    def save(self, namespace: str, profile: dict[str, Any]) -> None:
        del namespace, profile


def test_memory_recall_does_not_turn_lease_error_into_vector_fallback() -> None:
    manager = MemoryManager(
        config=MemoryConfig(vector_adapter=_LeaseRejectingVectorAdapter()),
        store=InMemorySessionStore(),
    )

    with pytest.raises(ActiveExecutionLeaseError):
        manager.recall_memory(
            session_id="fatal-boundary-session",
            query="what happened?",
            include_short_term=True,
            include_long_term=False,
        )


def test_memory_commit_does_not_turn_lease_error_into_vector_fallback() -> None:
    manager = MemoryManager(
        config=MemoryConfig(vector_adapter=_LeaseRejectingVectorAdapter()),
        store=InMemorySessionStore(),
    )

    with pytest.raises(ActiveExecutionLeaseError):
        manager.commit_messages(
            "fatal-boundary-session",
            [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ],
        )


def test_memory_commit_rejects_out_of_domain_fence_before_external_writes() -> None:
    adapter = _RecordingVectorAdapter()
    manager = MemoryManager(
        config=MemoryConfig(vector_adapter=adapter),
        store=InMemorySessionStore(),
    )

    with pytest.raises(ValueError, match="own session_id or a descendant"):
        manager.commit_messages(
            "target-session",
            [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ],
            execution_fence=ExecutionFence(
                execution_id="different-session",
                owner_id="owner-a",
                fencing_token=1,
            ),
        )

    assert adapter.add_calls == 0


def test_long_term_extractor_does_not_turn_lease_error_into_fallback() -> None:
    def reject_extraction(*_: Any, **__: Any) -> dict[str, Any]:
        raise _lease_error()

    manager = MemoryManager(
        config=MemoryConfig(
            long_term=LongTermMemoryConfig(
                profile_store=_ProfileStore(),
                vector_adapter=_NoopLongTermVectorAdapter(),
                extractor=reject_extraction,
            )
        ),
        store=InMemorySessionStore(),
    )

    with pytest.raises(ActiveExecutionLeaseError):
        manager.commit_messages(
            "fatal-boundary-session",
            [
                {"role": "user", "content": "remember this"},
                {"role": "assistant", "content": "remembered"},
            ],
        )


def test_memory_component_initialization_does_not_swallow_lease_error() -> None:
    runtime = KernelMemoryRuntime.from_config()

    def reject_initialization() -> None:
        raise _lease_error()

    runtime.memory_manager.ensure_long_term_components = reject_initialization  # type: ignore[method-assign]

    with pytest.raises(ActiveExecutionLeaseError):
        runtime.ensure_long_term_components()


def test_summary_optimizer_does_not_turn_lease_error_into_fallback() -> None:
    def reject_summary(*_: Any) -> str:
        raise _lease_error()

    loop = KernelLoop(
        harnesses=[
            LlmSummaryOptimizer(
                LlmSummaryOptimizerConfig(
                    summary_trigger_pct=0.1,
                    summary_target_pct=0.1,
                    max_summary_chars=200,
                    summary_generator=reject_summary,
                )
            )
        ]
    )
    state = loop.seed_state(
        [
            {"role": "user", "content": "old " * 800},
            {"role": "assistant", "content": "answer " * 800},
            {"role": "user", "content": "new"},
            {"role": "assistant", "content": "response"},
        ],
        model="fake",
        max_context_window_tokens=300,
    )

    with pytest.raises(ActiveExecutionLeaseError):
        loop.dispatch_phase(state, phase="before_model", event={"toolkit": Toolkit()})


def test_result_optimizer_does_not_turn_lease_error_into_budget_fallback() -> None:
    call = ToolCall(call_id="call-1", name="large_result", arguments={})
    toolkit = Toolkit()

    def reject_optimization(
        payload: Any,
        context: ToolHistoryOptimizationContext,
    ) -> Any:
        del payload, context
        raise _lease_error()

    toolkit.register(
        lambda: {"unused": True},
        name="large_result",
        parameters=[],
        history_result_optimizer=reject_optimization,
    )
    message = get_provider_message_builder("openai").build_tool_result_message(
        tool_call=call,
        tool_result={"blob": "x" * 2_000},
    )

    with pytest.raises(ActiveExecutionLeaseError):
        ToolResultBudgetController(
            ToolResultBudgetConfig(
                max_result_chars=160,
                max_batch_chars=1_000,
                preview_chars=24,
                min_chars_to_budget=40,
            )
        ).budget_messages(
            provider="openai",
            toolkit=toolkit,
            tool_calls=[call],
            result_messages=[message],
            session_id="fatal-boundary-session",
        )
