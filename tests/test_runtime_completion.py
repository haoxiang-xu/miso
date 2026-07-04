from __future__ import annotations

import inspect

from unchain.agent.builder import PreparedAgent
from unchain.kernel.types import KernelRunResult


def test_runtime_completion_policy_runner_retries_completed_result_until_complete():
    from unchain.agent import CompletionPolicy as AgentCompletionPolicy
    from unchain.runtime import CompletionEvaluation, CompletionPolicy, CompletionPolicyRunner

    first = KernelRunResult(
        messages=[{"role": "assistant", "content": "draft answer"}],
        status="completed",
        consumed_tokens=2,
        previous_response_id="resp_draft",
        iteration=1,
    )
    second = KernelRunResult(
        messages=[{"role": "assistant", "content": "final answer"}],
        status="completed",
        consumed_tokens=3,
        previous_response_id="resp_final",
        iteration=1,
    )
    events: list[dict] = []
    calls: list[dict] = []

    def validate(result: KernelRunResult) -> CompletionEvaluation:
        final_text = str(result.messages[-1].get("content") or "")
        if "final" in final_text:
            return CompletionEvaluation(complete=True)
        return CompletionEvaluation(complete=False, feedback="Revise with the final answer.")

    def run_once(
        *,
        messages: list[dict],
        payload: dict | None,
        previous_response_id: str | None,
        max_iterations: int,
    ) -> KernelRunResult:
        calls.append(
            {
                "messages": messages,
                "payload": payload,
                "previous_response_id": previous_response_id,
                "max_iterations": max_iterations,
            }
        )
        return second

    runner = CompletionPolicyRunner(
        policy=CompletionPolicy(validator=validate, max_repair_turns=1, repair_max_iterations=4),
        run_once=run_once,
        event_callback=events.append,
        run_id="run-completion",
    )

    result = runner.apply(first, payload={"store": False}, max_iterations=2)

    assert AgentCompletionPolicy is CompletionPolicy
    assert result is second
    assert calls == [
        {
            "messages": [
                {"role": "assistant", "content": "draft answer"},
                {"role": "user", "content": "Revise with the final answer."},
            ],
            "payload": {"store": False},
            "previous_response_id": "resp_draft",
            "max_iterations": 4,
        }
    ]
    assert [event["type"] for event in events] == [
        "completion_policy_evaluated",
        "completion_policy_retry",
        "completion_policy_evaluated",
    ]


def test_prepared_agent_delegates_completion_policy_to_runtime_runner():
    source = inspect.getsource(PreparedAgent)

    assert "CompletionPolicyRunner" in source
    assert "def _apply_completion_policy" not in source
    assert "completion_policy_retry" not in source
