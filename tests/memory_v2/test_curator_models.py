from __future__ import annotations

from dataclasses import replace

import pytest

from unchain.journal import ResourceRef
from unchain.memory.curator import (
    CandidateOrigin,
    CandidateOutcome,
    CandidateResolution,
    CandidateStatus,
    ConsolidationJob,
    ConsolidationJobStatus,
    CuratorLeaseFence,
    CuratorPolicy,
    FrozenCandidateSnapshot,
    Lease,
    RootRunCompletion,
    RunCaptureStatus,
    SourceRunStatus,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def event_ref(value: str) -> ResourceRef:
    return ResourceRef("context_event", value, 1)


def candidate_ref(value: str, revision: int = 1) -> ResourceRef:
    return ResourceRef("memory_candidate", value, revision)


def snapshot(value: str = "candidate-a") -> FrozenCandidateSnapshot:
    return FrozenCandidateSnapshot(
        candidate_ref=candidate_ref(value),
        target_space_id="space-chat-a",
        binding_revision=1,
        outcome=CandidateStatus.QUEUED,
        origin=CandidateOrigin.AGENT_PROPOSAL,
        target_path=f"/notes/{value}.md",
        name=f"{value}.md",
        description="Durable project decision",
        kind="file",
        media_type="text/markdown",
        content_ref=candidate_ref(value),
        source_refs=(event_ref(f"event-{value}"),),
        payload_sha256=SHA_A,
        content_sha256=SHA_B,
        byte_length=42,
    )


def completion(**overrides) -> RootRunCompletion:
    values = {
        "session_id": "session-a",
        "attempt_id": "attempt-a",
        "run_id": "run-a",
        "is_root_run": True,
        "run_status": SourceRunStatus.COMPLETED,
        "capture_status": RunCaptureStatus.COMPLETE,
    }
    values.update(overrides)
    return RootRunCompletion(**values)


def test_status_values_match_schema_v4_exactly():
    assert {item.value for item in ConsolidationJobStatus} == {
        "pending",
        "leased",
        "completed",
        "failed",
        "cancelled",
    }
    assert {item.value for item in CandidateStatus} == {
        "pending",
        "queued",
        "processing",
        "applied",
        "awaiting_user",
        "isolated",
        "rejected",
        "superseded",
    }
    assert {item.value for item in CandidateOutcome} == {
        "applied",
        "awaiting_user",
        "isolated",
        "rejected",
        "superseded",
    }


@pytest.mark.parametrize(
    "origin",
    [
        CandidateOrigin.AGENT_PROPOSAL,
        CandidateOrigin.USER_EXPLICIT,
        CandidateOrigin.CHECKPOINT,
    ],
)
def test_only_approved_candidate_origins_can_form_frozen_snapshots(origin):
    candidate = snapshot()
    assert replace(candidate, origin=origin).origin is origin


def test_unknown_candidate_origin_is_rejected():
    candidate = snapshot()
    with pytest.raises(ValueError, match="candidate origin"):
        FrozenCandidateSnapshot(
            candidate_ref=candidate.candidate_ref,
            origin="automatic_recall",
            target_path=candidate.target_path,
            name=candidate.name,
            description=candidate.description,
            kind=candidate.kind,
            media_type=candidate.media_type,
            source_refs=candidate.source_refs,
            payload_sha256=candidate.payload_sha256,
            content_sha256=candidate.content_sha256,
            byte_length=candidate.byte_length,
        )


def test_candidate_snapshot_is_metadata_only_and_immutable():
    candidate = snapshot()
    encoded = candidate.to_dict()

    assert "content" not in encoded
    assert encoded["candidate_ref"]["kind"] == "memory_candidate"
    assert encoded["source_refs"][0]["kind"] == "context_event"
    with pytest.raises((AttributeError, TypeError)):
        candidate.description = "changed"


def test_root_completion_key_covers_full_execution_identity():
    first = completion()
    assert first.trigger_key == completion().trigger_key
    assert first.trigger_key != completion(attempt_id="attempt-b").trigger_key
    assert first.trigger_key != completion(run_id="run-b").trigger_key


def test_job_invariants_require_exact_lease_shape_and_candidate_cap():
    candidate = snapshot()
    job = ConsolidationJob.pending(
        job_id="job-a",
        trigger=completion(),
        candidates=(candidate,),
        operation_id="curator.enqueue.abc",
        now_ms=10,
    )
    assert job.status is ConsolidationJobStatus.PENDING
    assert job.lease is None

    leased = job.with_lease(
        Lease(owner="worker-a", token="lease-a", expires_at_ms=1010),
        revision=2,
        now_ms=10,
    )
    assert leased.status is ConsolidationJobStatus.LEASED
    assert leased.attempt_count == 1

    with pytest.raises(ValueError, match="200"):
        ConsolidationJob.pending(
            job_id="job-too-large",
            trigger=completion(run_id="run-large"),
            candidates=tuple(
                snapshot(f"candidate-{index}") for index in range(201)
            ),
            operation_id="curator.enqueue.large",
            now_ms=10,
        )


def test_lease_fence_binds_scope_job_revision_and_opaque_lease_token():
    fence = CuratorLeaseFence(
        binding_id="binding-a",
        job_id="job-a",
        job_revision=3,
        lease_owner="worker-a",
        lease_token="lease-a",
    )
    assert fence.job_revision == 3
    with pytest.raises(ValueError, match="lease token"):
        CuratorLeaseFence(
            binding_id="binding-a",
            job_id="job-a",
            job_revision=3,
            lease_owner="worker-a",
            lease_token="",
        )


def test_review_outcome_requires_server_review_ref_and_nonempty_diff():
    ref = candidate_ref("candidate-a")
    review_ref = ResourceRef("memory_review", "review-a", 1, "space-chat-a")
    resolution = CandidateResolution(
        candidate_ref=ref,
        target_space_id="space-chat-a",
        outcome=CandidateOutcome.AWAITING_USER,
        result_ref=review_ref,
        review_diff={"mode": "overwrite", "fields": ["description"]},
    )
    assert resolution.review_diff["mode"] == "overwrite"

    with pytest.raises(ValueError, match="review diff"):
        CandidateResolution(
            candidate_ref=ref,
            target_space_id="space-chat-a",
            outcome=CandidateOutcome.AWAITING_USER,
            result_ref=review_ref,
            review_diff={},
        )
    with pytest.raises(ValueError, match="review reference"):
        CandidateResolution(
            candidate_ref=ref,
            target_space_id="space-chat-a",
            outcome=CandidateOutcome.AWAITING_USER,
            result_ref=ResourceRef("memory", "entry-a", 1, "space-chat-a"),
            review_diff={"mode": "overwrite"},
        )


def test_resolution_contract_cannot_represent_automatic_long_term_promotion():
    with pytest.raises(ValueError, match="result reference"):
        CandidateResolution(
            candidate_ref=candidate_ref("candidate-a"),
            target_space_id="space-chat-a",
            outcome=CandidateOutcome.APPLIED,
            result_ref=ResourceRef(
                "promotion",
                "promotion-a",
                1,
                "space-chat-a",
            ),
        )

    policy = CuratorPolicy.p0()
    assert policy.require_candidate_bound_toolkit is True
    assert policy.new_paths_require_frozen_apply is True
    assert policy.conflicts_require_server_diff is True
    assert policy.allow_credentials is False
    assert policy.allow_long_term_write is False
    assert policy.allow_promotion_decision is False
    assert policy.allow_task_state_write is False
    assert policy.conflicts_require_user_review is True
