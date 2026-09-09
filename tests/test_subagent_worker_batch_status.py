import pytest

from unchain.subagents.plugin import _aggregate_worker_batch_status
from unchain.subagents.types import SubagentResult


def _result(status):
    return SubagentResult(
        mode="worker",
        agent_name="worker",
        template_name="Worker",
        status=status,
    )


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["completed", "completed"], "completed"),
        (["failed", "failed"], "failed"),
        (["timeout", "timeout"], "failed"),
        (["failed", "timeout"], "failed"),
        (["completed", "failed"], "partial_failure"),
        (["completed", "needs_clarification"], "partial_failure"),
        (["needs_clarification"], "partial_failure"),
    ],
)
def test_aggregate_worker_batch_status(statuses, expected):
    assert _aggregate_worker_batch_status([_result(status) for status in statuses]) == expected


def test_aggregate_worker_batch_status_treats_empty_batch_as_failed():
    assert _aggregate_worker_batch_status([]) == "failed"


def test_subagent_result_keeps_run_bundles_out_of_model_visible_payload() -> None:
    result = SubagentResult(
        mode="worker",
        agent_name="worker",
        template_name="Worker",
        status="failed",
        subagent_state={
            "blackboards": {"default": [{"item_id": "safe-summary"}]},
            "run_bundles": {"bundle-id": {"private": "accounting"}},
        },
    )

    assert "run_bundles" not in result.to_dict()["subagent_state"]
    assert result.to_record_dict()["subagent_state"]["run_bundles"] == {
        "bundle-id": {"private": "accounting"}
    }
