from __future__ import annotations

from unchain.subagents.executor import SubagentExecutor
from unchain.subagents.types import SubagentResult


async def _async_generator():
    yield "event"


def test_execute_batch_accepts_worker_items_with_runtime_handles() -> None:
    runtime_handle = _async_generator()
    executor = SubagentExecutor(max_parallel_workers=1)
    seen_handles: list[object] = []

    def run_item(index: int, item: dict) -> SubagentResult:
        seen_handles.append(item["runtime_handle"])
        return SubagentResult(
            mode="worker",
            agent_name=f"worker-{index}",
            template_name=None,
            status="completed",
            output=str(item["task"]),
            summary=str(item["task"]),
        )

    results = executor.execute_batch(
        items=[{"task": "inspect repo", "runtime_handle": runtime_handle}],
        run_item=run_item,
    )

    assert seen_handles == [runtime_handle]
    assert [result.status for result in results] == ["completed"]
