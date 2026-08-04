from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from unchain.journal import ResourceRef
from unchain.memory.curator import CandidateStatus, ConsolidationJobStatus
from unchain.persistence.sqlite_curator_review_decision_v2 import (
    MemoryReviewDecisionStatus,
    SQLiteCuratorReviewDecisionV2Conflict,
    SQLiteCuratorReviewDecisionV2Error,
    SQLiteCuratorReviewDecisionV2IntegrityError,
    SQLiteCuratorReviewDecisionV2Store,
)

from .test_sqlite_curator_query_v2 import (
    _open_stack,
    _query,
    _review_content_ref,
    _seed_pending_review,
    _seed_prepared_review,
)


def _decisions(stack):
    return SQLiteCuratorReviewDecisionV2Store(
        database_path=stack["database"],
        object_directory=stack["objects"],
        clock_ms=stack["clock"],
    ).bind(
        binding_id="binding-chat-a",
        owner_chat_id="chat-a",
        target_space_id="space-chat-a",
    )


def _request(review_ref, candidate, target, *, operation_id, decision="apply"):
    return {
        "review_id": review_ref.resource_id,
        "decision": decision,
        "expected_review_revision": review_ref.revision,
        "expected_candidate_revision": candidate.binding_revision,
        "expected_target_revision": target.revision,
        "expected_space_revision": 2,
        "decision_reason": f"user selected {decision}",
        "operation_id": operation_id,
    }


def test_apply_is_atomic_replayable_and_preserves_review_content(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, target, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    request = _request(
        review_ref,
        published,
        target,
        operation_id="apply-published-review",
    )

    receipt = _decisions(stack).decide(**request)

    assert receipt.status is MemoryReviewDecisionStatus.APPLIED
    assert receipt.proposal_ref == review_ref
    assert receipt.review_ref == ResourceRef(
        "memory_review",
        review_ref.resource_id,
        2,
        "space-chat-a",
    )
    assert receipt.target_entry_ref == ResourceRef(
        "memory", target.entry_id, target.revision, "space-chat-a"
    )
    assert receipt.applied_entry_ref == ResourceRef(
        "memory", target.entry_id, target.revision + 1, "space-chat-a"
    )
    assert receipt.space_revision_before == 2
    assert receipt.space_revision_after == 3
    assert receipt.replayed is False

    applied = stack["workspace"].repository.read_current_entry(
        entry_id=target.entry_id
    )
    assert applied.revision == target.revision + 1
    assert applied.description == "Confirmed durable policy for a long-running task"
    assert stack["workspace"].read(
        ResourceRef("memory", applied.entry_id, applied.revision, applied.space_id)
    ).data == b"new policy"

    query = _query(stack)
    candidate = query.get_candidate(
        candidate_id=published.candidate_ref.resource_id
    )
    assert candidate.outcome is CandidateStatus.APPLIED
    assert candidate.binding_revision == published.binding_revision + 1
    assert candidate.result_ref == receipt.applied_entry_ref
    job = query.get_job(job_id=completed.job_id)
    assert job.status is ConsolidationJobStatus.COMPLETED
    assert job.revision == completed.revision + 1
    assert candidate in job.candidates
    assert query.list_pending_reviews() == ()
    assert query.read_review_content(
        ref=_review_content_ref(review_ref, "proposed")
    ).data == b"new policy"

    reopened = _decisions(stack)
    assert reopened.get_decision(review_id=review_ref.resource_id) == receipt
    replay = reopened.decide(**request)
    assert replay == receipt.__class__(
        **{
            **{
                field_name: getattr(receipt, field_name)
                for field_name in receipt.__dataclass_fields__
                if field_name != "replayed"
            },
            "replayed": True,
        }
    )


def test_reject_uses_proposal_fences_but_tolerates_workspace_drift(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, target, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    drifted = stack["workspace"].write_markdown(
        entry_ref=ResourceRef(
            "memory", target.entry_id, target.revision, target.space_id
        ),
        path=target.path,
        description="A newer target the rejection must preserve",
        content="newer target",
        expected_space_revision=2,
        source_refs=(stack["source_a"],),
        operation_id="drift-target-before-reject",
    )

    receipt = _decisions(stack).decide(
        **_request(
            review_ref,
            published,
            target,
            operation_id="reject-published-review",
            decision="reject",
        )
    )

    assert receipt.status is MemoryReviewDecisionStatus.REJECTED
    assert receipt.applied_entry_ref is None
    assert receipt.space_revision_before == 3
    assert receipt.space_revision_after == 3
    assert stack["workspace"].repository.read_current_entry(
        entry_id=target.entry_id
    ) == drifted
    candidate = _query(stack).get_candidate(
        candidate_id=published.candidate_ref.resource_id
    )
    assert candidate.outcome is CandidateStatus.REJECTED
    assert _query(stack).read_review_content(
        ref=_review_content_ref(review_ref, "diff")
    ).total_bytes > 0


@pytest.mark.parametrize(
    ("field_name", "replacement", "code"),
    (
        ("expected_review_revision", 2, "review_revision_conflict"),
        ("expected_candidate_revision", 4, "candidate_revision_conflict"),
        ("expected_target_revision", 2, "target_revision_conflict"),
        ("expected_space_revision", 3, "space_revision_conflict"),
    ),
)
def test_apply_fence_conflict_rolls_back_every_head(
    tmp_path: Path,
    field_name: str,
    replacement: int,
    code: str,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, target, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    request = _request(
        review_ref,
        published,
        target,
        operation_id=f"stale-{field_name}",
    )
    request[field_name] = replacement
    decisions = _decisions(stack)

    with pytest.raises(SQLiteCuratorReviewDecisionV2Conflict) as conflict:
        decisions.decide(**request)
    assert conflict.value.code == code

    assert stack["workspace"].space.revision == 2
    assert stack["workspace"].repository.read_current_entry(
        entry_id=target.entry_id
    ) == target
    query = _query(stack)
    assert query.get_candidate(
        candidate_id=published.candidate_ref.resource_id
    ) == published
    assert query.get_job(job_id=completed.job_id) == completed
    with pytest.raises(SQLiteCuratorReviewDecisionV2Error) as missing:
        decisions.get_decision(review_id=review_ref.resource_id)
    assert missing.value.code == "memory_review_decision_not_found"


def test_prepared_review_cannot_be_decided(tmp_path: Path) -> None:
    stack = _open_stack(tmp_path)
    review, _, claimed, _ = _seed_prepared_review(stack)
    review_ref = review["result_ref"]
    target_ref = ResourceRef.from_dict(review["review_diff"]["target"]["ref"])

    with pytest.raises(SQLiteCuratorReviewDecisionV2Conflict) as conflict:
        _decisions(stack).decide(
            review_id=review_ref.resource_id,
            decision="apply",
            expected_review_revision=1,
            expected_candidate_revision=claimed.candidates[0].binding_revision,
            expected_target_revision=target_ref.revision,
            expected_space_revision=2,
            decision_reason="not yet published",
            operation_id="decide-prepared-review",
        )
    assert conflict.value.code == "memory_review_not_published"


def test_operation_idempotency_is_exact_and_second_decision_is_rejected(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, target, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    request = _request(
        review_ref,
        published,
        target,
        operation_id="exact-decision-operation",
    )
    decisions = _decisions(stack)
    decisions.decide(**request)

    with pytest.raises(SQLiteCuratorReviewDecisionV2Conflict) as operation:
        decisions.decide(**{**request, "decision_reason": "changed payload"})
    assert operation.value.code == "review_decision_operation_payload_conflict"

    with pytest.raises(SQLiteCuratorReviewDecisionV2Conflict) as decided:
        decisions.decide(
            **{
                **request,
                "operation_id": "second-decision-operation",
            }
        )
    assert decided.value.code == "memory_review_already_decided"


def test_late_candidate_failure_rolls_back_workspace_and_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, target, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    decisions = _decisions(stack)

    def fail_candidate_transition(*_args, **_kwargs):
        raise RuntimeError("injected candidate transition failure")

    monkeypatch.setattr(
        decisions._curator,
        "_write_candidate_transition",
        fail_candidate_transition,
    )
    with pytest.raises(RuntimeError, match="injected candidate"):
        decisions.decide(
            **_request(
                review_ref,
                published,
                target,
                operation_id="rollback-after-workspace-write",
            )
        )

    assert stack["workspace"].space.revision == 2
    assert stack["workspace"].repository.read_current_entry(
        entry_id=target.entry_id
    ) == target
    query = _query(stack)
    assert query.get_candidate(
        candidate_id=published.candidate_ref.resource_id
    ) == published
    assert query.get_job(job_id=completed.job_id) == completed
    with pytest.raises(SQLiteCuratorReviewDecisionV2Error):
        decisions.get_decision(review_id=review_ref.resource_id)


def test_missing_candidate_object_fails_apply_and_rolls_back_every_head(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, target, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    with sqlite3.connect(stack["database"]) as connection:
        candidate_head_before = connection.execute(
            """
            SELECT current_record_revision, status FROM candidates
            WHERE candidate_id = ?
            """,
            (published.candidate_ref.resource_id,),
        ).fetchone()
        job_head_before = connection.execute(
            """
            SELECT current_revision, status FROM consolidation_jobs
            WHERE job_id = ?
            """,
            (completed.job_id,),
        ).fetchone()
    (stack["objects"] / published.content_sha256).unlink()
    decisions = _decisions(stack)

    with pytest.raises(SQLiteCuratorReviewDecisionV2IntegrityError):
        decisions.decide(
            **_request(
                review_ref,
                published,
                target,
                operation_id="missing-candidate-object",
            )
        )

    assert stack["workspace"].space.revision == 2
    assert stack["workspace"].repository.read_current_entry(
        entry_id=target.entry_id
    ) == target
    with sqlite3.connect(stack["database"]) as connection:
        candidate_head_after = connection.execute(
            """
            SELECT current_record_revision, status FROM candidates
            WHERE candidate_id = ?
            """,
            (published.candidate_ref.resource_id,),
        ).fetchone()
        job_head_after = connection.execute(
            """
            SELECT current_revision, status FROM consolidation_jobs
            WHERE job_id = ?
            """,
            (completed.job_id,),
        ).fetchone()
        assert candidate_head_after == candidate_head_before
        assert job_head_after == job_head_before
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_review_decisions"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM memory_review_operation_receipts"
        ).fetchone()[0] == 0
    with pytest.raises(SQLiteCuratorReviewDecisionV2Error) as missing:
        decisions.get_decision(review_id=review_ref.resource_id)
    assert missing.value.code == "memory_review_decision_not_found"


@pytest.mark.parametrize(
    "corruption",
    (
        "proposal",
        "candidate_successor",
        "job_successor",
        "entry_successor",
        "object",
    ),
)
def test_get_and_replay_fail_closed_when_committed_successor_is_corrupt(
    tmp_path: Path,
    corruption: str,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, target, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    request = _request(
        review_ref,
        published,
        target,
        operation_id=f"corrupt-successor-{corruption}",
    )
    decisions = _decisions(stack)
    receipt = decisions.decide(**request)

    if corruption == "object":
        (stack["objects"] / published.content_sha256).unlink()
    else:
        statements = {
            "proposal": (
                "DELETE FROM memory_review_proposals WHERE review_id = ?",
                (review_ref.resource_id,),
            ),
            "candidate_successor": (
                """
                DELETE FROM candidate_bindings
                WHERE candidate_id = ? AND binding_revision = ?
                """,
                (
                    published.candidate_ref.resource_id,
                    receipt.candidate_binding_revision_after,
                ),
            ),
            "job_successor": (
                """
                DELETE FROM consolidation_job_revisions
                WHERE job_id = ? AND revision = ?
                """,
                (completed.job_id, receipt.job_revision_after),
            ),
            "entry_successor": (
                """
                DELETE FROM entry_revisions
                WHERE space_id = ? AND entry_id = ? AND revision = ?
                """,
                (
                    "space-chat-a",
                    target.entry_id,
                    receipt.applied_entry_ref.revision,
                ),
            ),
        }
        statement, parameters = statements[corruption]
        with sqlite3.connect(stack["database"]) as connection:
            connection.execute(statement, parameters)

    with pytest.raises(SQLiteCuratorReviewDecisionV2IntegrityError):
        decisions.get_decision(review_id=review_ref.resource_id)
    with pytest.raises(SQLiteCuratorReviewDecisionV2IntegrityError):
        decisions.decide(**request)


def test_replay_uses_immutable_successors_after_workspace_advances(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, target, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    request = _request(
        review_ref,
        published,
        target,
        operation_id="decision-before-workspace-progress",
    )
    decisions = _decisions(stack)
    receipt = decisions.decide(**request)

    stack["workspace"].write_markdown(
        path="/decisions/later.md",
        description="A later unrelated workspace entry",
        content="later",
        expected_space_revision=receipt.space_revision_after,
        source_refs=(stack["source_b"],),
        operation_id="later-unrelated-workspace-write",
    )

    assert decisions.get_decision(review_id=review_ref.resource_id) == receipt
    assert decisions.decide(**request).replayed is True
