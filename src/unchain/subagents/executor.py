from __future__ import annotations

import concurrent.futures
import copy
from dataclasses import dataclass
from typing import Callable

from .types import SubagentResult


@dataclass(frozen=True)
class SubagentExecutor:
    max_parallel_workers: int = 4
    worker_timeout_seconds: float = 30.0

    def execute_batch(
        self,
        *,
        items: list[dict],
        run_item: Callable[[int, dict], SubagentResult],
    ) -> list[SubagentResult]:
        if not items:
            return []
        max_workers = max(1, min(int(self.max_parallel_workers), len(items)))
        results: list[SubagentResult | None] = [None] * len(items)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_item, index, copy.deepcopy(item)): index
                for index, item in enumerate(items)
            }
            try:
                for future in concurrent.futures.as_completed(
                    futures,
                    timeout=float(self.worker_timeout_seconds),
                ):
                    index = futures[future]
                    results[index] = future.result()
            except concurrent.futures.TimeoutError:
                for future, index in futures.items():
                    if results[index] is None:
                        future.cancel()
                        results[index] = SubagentResult(
                            mode="worker",
                            agent_name="",
                            template_name=None,
                            status="timeout",
                            error="worker batch timed out",
                        )
        return [result if isinstance(result, SubagentResult) else SubagentResult(mode="worker", agent_name="", template_name=None, status="failed", error="worker execution failed") for result in results]
