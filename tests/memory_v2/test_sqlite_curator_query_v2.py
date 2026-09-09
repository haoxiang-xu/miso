from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from unchain.journal import OperationRef, ResourceRef
from unchain.memory.curator import (
    CandidateOutcome,
    CandidateResolution,
    CandidateStatus,
    ConsolidationJobStatus,
    CuratorCoordinator,
    EnqueueDisposition,
    RootRunCompletion,
    RunCaptureStatus,
    SourceRunStatus,
)
from unchain.memory.toolkit import CandidateProposalRequest, MemoryToolkitRunBinding
from unchain.memory.workspace import MemorySpace, MemoryWorkspaceService
from unchain.persistence.sqlite_curator_query_v2 import (
    MemoryReviewStatus,
    SQLiteCuratorQueryV2Error,
    SQLiteCuratorQueryV2IntegrityError,
    SQLiteCuratorQueryV2Store,
)
from unchain.persistence.sqlite_curator_v2 import SQLiteCuratorV2Store
from unchain.persistence.sqlite_memory_host_v2 import (
    SQLiteConsolidationCapabilityFactory,
)
from unchain.persistence.sqlite_memory_v2 import SQLiteMemoryV2Store
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store

from .fakes import FakeReferenceAuthorizer
from .test_memory_toolkit_security import FakeCodec, FakeContext


class _Clock:
    def __init__(self, now_ms: int = 1_000) -> None:
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms


def _binding(
    *,
    binding_id: str = "binding-chat-a",
    session_id: str = "session-a",
    attempt_id: str = "attempt-a",
    run_id: str = "run-a",
) -> MemoryToolkitRunBinding:
    return MemoryToolkitRunBinding(
        binding_id=binding_id,
        session_id=session_id,
        attempt_id=attempt_id,
        run_id=run_id,
    )


def _completion(binding: MemoryToolkitRunBinding) -> RootRunCompletion:
    return RootRunCompletion(
        session_id=binding.session_id,
        attempt_id=binding.attempt_id,
        run_id=binding.run_id,
        is_root_run=True,
        run_status=SourceRunStatus.COMPLETED,
        capture_status=RunCaptureStatus.COMPLETE,
    )


def _space(
    space_id: str = "space-chat-a",
    name: str = "Chat memory",
) -> MemorySpace:
    return MemorySpace(
        space_id=space_id,
        namespace="chat",
        name=name,
        description="Memory V2 query test workspace",
        revision=1,
    )


def _proposal(
    binding: MemoryToolkitRunBinding,
    *,
    source_ref: ResourceRef,
    operation_id: str,
    path: str,
    content: bytes = b"new durable policy",
) -> CandidateProposalRequest:
    return CandidateProposalRequest(
        path=path,
        description="Confirmed durable policy for a long-running task",
        kind="markdown",
        content=content,
        media_type="text/markdown",
        url="",
        source_refs=(source_ref,),
        rationale="Preserve the confirmed decision",
        confidence=0.98,
        sensitivity="normal",
        operation_id=operation_id,
    )


def _open_stack(root: Path):
    database = root / "context_v2.sqlite3"
    objects = root / "objects"
    clock = _Clock()
    source_a = ResourceRef("context_event", "event-a", 1)
    source_b = ResourceRef("context_event", "event-b", 1)
    SQLiteContextV2Store(database_path=database, object_directory=objects)
    memory_store = SQLiteMemoryV2Store(
        database_path=database,
        object_directory=objects,
    )
    workspace_repository = memory_store.bind_workspace(
        space=_space(),
        owner_chat_id="chat-a",
    )
    workspace = MemoryWorkspaceService(
        repository=workspace_repository,
        mutations=workspace_repository,
        content=workspace_repository,
        history=workspace_repository,
        links=workspace_repository,
        references=FakeReferenceAuthorizer(
            "binding-chat-a", {source_a, source_b}
        ),
    )
    curator_store = SQLiteCuratorV2Store(
        database_path=database,
        object_directory=objects,
        clock_ms=clock,
    )
    repository = curator_store.bind_curation(
        binding_id="binding-chat-a",
        owner_chat_id="chat-a",
        target_space_id=_space().space_id,
    )
    factory = SQLiteConsolidationCapabilityFactory(
        binding_id="binding-chat-a",
        database_path=database,
        repository=repository,
        workspace=workspace,
        references=FakeCodec("binding-chat-a"),
        context=FakeContext("binding-chat-a"),
        clock_ms=clock,
    )
    return {
        "database": database,
        "objects": objects,
        "clock": clock,
        "source_a": source_a,
        "source_b": source_b,
        "workspace": workspace,
        "curator_store": curator_store,
        "repository": repository,
        "factory": factory,
    }


def _query(stack):
    return SQLiteCuratorQueryV2Store(
        database_path=stack["database"],
        object_directory=stack["objects"],
    ).bind(
        binding_id="binding-chat-a",
        owner_chat_id="chat-a",
        target_space_id="space-chat-a",
    )


def _propose(stack, binding, *, source_ref, operation_id, path, content=b"body"):
    return stack["repository"].bind_candidate_proposals(
        binding=binding
    ).propose(
        request=_proposal(
            binding,
            source_ref=source_ref,
            operation_id=operation_id,
            path=path,
            content=content,
        )
    )


def _enqueue_and_claim(stack, binding):
    coordinator = CuratorCoordinator(
        stack["repository"], clock_ms=stack["clock"]
    )
    enqueued = coordinator.enqueue(_completion(binding))
    assert enqueued.disposition is EnqueueDisposition.ENQUEUED
    claimed = coordinator.claim_next(
        worker_id=f"worker-{binding.run_id}",
        lease_ms=1_000,
        operation_id=f"claim-{binding.run_id}",
    )
    assert claimed is not None
    return claimed


def _capabilities(stack, job):
    digest = hashlib.sha256(
        f"{job.job_id}:{job.revision}".encode("utf-8")
    ).hexdigest()
    curator_binding = MemoryToolkitRunBinding(
        binding_id="binding-chat-a",
        session_id=job.trigger.session_id,
        attempt_id=f"memory-curator-attempt-{digest}",
        run_id=f"memory-curator-run-{digest}",
    )
    guard = stack["repository"].bind_mutation_guard(job=job)
    return stack["factory"].build(
        binding=curator_binding,
        job=job,
        mutation_guard=guard,
    )


def test_current_candidates_jobs_filters_scope_and_restart(tmp_path: Path) -> None:
    stack = _open_stack(tmp_path)
    run_a = _binding()
    processing = _propose(
        stack,
        run_a,
        source_ref=stack["source_a"],
        operation_id="candidate-a",
        path="/decisions/a.md",
    )
    job = _enqueue_and_claim(stack, run_a)
    run_b = _binding(
        session_id="session-b",
        attempt_id="attempt-b",
        run_id="run-b",
    )
    pending = _propose(
        stack,
        run_b,
        source_ref=stack["source_b"],
        operation_id="candidate-b",
        path="/decisions/b.md",
    )

    query = _query(stack)
    assert [item.candidate_ref.resource_id for item in query.list_candidates()] == [
        pending.candidate_ref.resource_id,
        processing.candidate_ref.resource_id,
    ]
    assert query.list_candidates(status=CandidateStatus.PENDING) == (pending,)
    processing_current = query.list_candidates(
        status=CandidateStatus.PROCESSING
    )
    assert len(processing_current) == 1
    assert processing_current[0].candidate_ref == processing.candidate_ref
    assert query.get_candidate(
        candidate_id=processing.candidate_ref.resource_id
    ) == processing_current[0]
    assert query.list_jobs(status=ConsolidationJobStatus.LEASED) == (job,)
    assert query.get_job(job_id=job.job_id) == job

    reopened = _query(stack)
    assert reopened.get_job(job_id=job.job_id) == job
    assert reopened.get_candidate(
        candidate_id=pending.candidate_ref.resource_id
    ) == pending

    with pytest.raises(TypeError, match="CandidateStatus"):
        query.list_candidates(status="pending")
    with pytest.raises(TypeError, match="ConsolidationJobStatus"):
        query.list_jobs(status="leased")
    with pytest.raises(ValueError, match="between 1 and 500"):
        query.list_jobs(limit=0)
    with pytest.raises(SQLiteCuratorQueryV2Error, match="scope"):
        SQLiteCuratorQueryV2Store(
            database_path=stack["database"],
            object_directory=stack["objects"],
        ).bind(
            binding_id="binding-chat-a",
            owner_chat_id="chat-foreign",
            target_space_id="space-chat-a",
        )

    foreign = stack["curator_store"].bind_curation(
        binding_id="binding-chat-b",
        owner_chat_id="chat-b",
        target_space_id="space-chat-b",
    )
    foreign_binding = _binding(
        binding_id="binding-chat-b",
        session_id="session-foreign",
        attempt_id="attempt-foreign",
        run_id="run-foreign",
    )
    foreign.bind_candidate_proposals(binding=foreign_binding).propose(
        request=_proposal(
            foreign_binding,
            source_ref=ResourceRef("context_event", "event-foreign", 1),
            operation_id="candidate-foreign",
            path="/foreign.md",
        )
    )
    assert len(query.list_candidates()) == 2
    with pytest.raises(SQLiteCuratorQueryV2Error, match="scope"):
        query.get_candidate(candidate_id="candidate-does-not-belong")


@pytest.mark.parametrize("corruption", ["candidate", "job", "object"])
def test_candidate_and_job_hash_corruption_fails_closed(
    tmp_path: Path,
    corruption: str,
) -> None:
    stack = _open_stack(tmp_path)
    binding = _binding()
    candidate = _propose(
        stack,
        binding,
        source_ref=stack["source_a"],
        operation_id="candidate-a",
        path="/decisions/a.md",
        content=b"hash protected candidate content",
    )
    job = _enqueue_and_claim(stack, binding)
    query = _query(stack)

    if corruption == "candidate":
        with sqlite3.connect(stack["database"]) as connection:
            connection.execute(
                """
                UPDATE candidate_revisions SET snapshot_sha256 = ?
                WHERE candidate_id = ?
                """,
                ("0" * 64, candidate.candidate_ref.resource_id),
            )
        read = lambda: query.get_candidate(
            candidate_id=candidate.candidate_ref.resource_id
        )
    elif corruption == "job":
        with sqlite3.connect(stack["database"]) as connection:
            connection.execute(
                "UPDATE consolidation_job_revisions SET job_sha256 = ? WHERE job_id = ?",
                ("0" * 64, job.job_id),
            )
        read = lambda: query.get_job(job_id=job.job_id)
    else:
        (stack["objects"] / candidate.content_sha256).write_bytes(b"tampered")
        read = lambda: query.get_candidate(
            candidate_id=candidate.candidate_ref.resource_id
        )

    with pytest.raises(SQLiteCuratorQueryV2IntegrityError):
        read()


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("owner_chat_id", "chat-foreign"),
        ("namespace", "user"),
        ("space_sha256", "0" * 64),
    ),
)
def test_curator_query_revalidates_workspace_chat_scope(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    stack = _open_stack(tmp_path)
    with sqlite3.connect(stack["database"]) as connection:
        connection.execute(
            f"UPDATE spaces SET {column} = ? WHERE space_id = ?",
            (value, "space-chat-a"),
        )

    with pytest.raises(SQLiteCuratorQueryV2IntegrityError):
        _query(stack)


def _seed_pending_review(stack):
    binding = _binding()
    existing = stack["workspace"].write_markdown(
        path="/decisions/context-policy.md",
        description="Existing target revision",
        content="old policy",
        expected_space_revision=1,
        source_refs=(stack["source_a"],),
        operation_id="seed-existing-target",
    )
    candidate = _propose(
        stack,
        binding,
        source_ref=stack["source_a"],
        operation_id="candidate-review",
        path="/decisions/context-policy.md",
        content=b"new policy",
    )
    claimed = _enqueue_and_claim(stack, binding)
    capabilities = _capabilities(stack, claimed)
    review = capabilities.consolidation.propose_review(
        job_id=claimed.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=claimed.candidates[0].binding_revision,
        target_entry_id=existing.entry_id,
        expected_target_revision=existing.revision,
        mode="overwrite",
        mutation_guard=capabilities.mutation_guard,
        operation_id="propose-review",
    )
    completed = stack["repository"].reconcile_and_complete(
        job=claimed,
        resolutions=(
            CandidateResolution(
                candidate_ref=candidate.candidate_ref,
                target_space_id="space-chat-a",
                outcome=CandidateOutcome.AWAITING_USER,
                result_ref=review["result_ref"],
                review_diff=review["review_diff"],
            ),
        ),
        mutation_guard=capabilities.mutation_guard,
        operation=OperationRef(
            "complete-review",
            hashlib.sha256(b"complete-review").hexdigest(),
        ),
        now_ms=stack["clock"](),
    )
    assert completed.status is ConsolidationJobStatus.COMPLETED
    return review["result_ref"], candidate, existing, completed


def _seed_prepared_review(stack):
    binding = _binding()
    existing = stack["workspace"].write_markdown(
        path="/decisions/prepared-review.md",
        description="Existing target revision",
        content="old policy",
        expected_space_revision=1,
        source_refs=(stack["source_a"],),
        operation_id="seed-prepared-review-target",
    )
    candidate = _propose(
        stack,
        binding,
        source_ref=stack["source_a"],
        operation_id="candidate-prepared-review",
        path="/decisions/prepared-review.md",
        content=b"new policy",
    )
    claimed = _enqueue_and_claim(stack, binding)
    capabilities = _capabilities(stack, claimed)
    review = capabilities.consolidation.propose_review(
        job_id=claimed.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=claimed.candidates[0].binding_revision,
        target_entry_id=existing.entry_id,
        expected_target_revision=existing.revision,
        mode="overwrite",
        mutation_guard=capabilities.mutation_guard,
        operation_id="propose-prepared-review",
    )
    return review, candidate, claimed, capabilities


def _review_content_ref(review_ref: ResourceRef, fragment: str) -> ResourceRef:
    return ResourceRef(
        "memory_review_content",
        review_ref.resource_id,
        1,
        fragment,
    )


def test_pending_review_is_immutable_scoped_restart_safe_and_hash_checked(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, candidate, existing, completed = _seed_pending_review(stack)
    query = _query(stack)

    listed = query.list_pending_reviews(status=MemoryReviewStatus.PENDING)
    assert len(listed) == 1
    review = listed[0]
    assert review.review_ref == review_ref
    assert review.candidate_ref == candidate.candidate_ref
    assert review.target_entry_ref == ResourceRef(
        "memory", existing.entry_id, existing.revision, "space-chat-a"
    )
    assert review.job_id == completed.job_id
    assert review.status is MemoryReviewStatus.PENDING
    assert review.review_diff["requires_user_confirmation"] is True
    with pytest.raises(TypeError):
        review.review_diff["mode"] = "changed"

    reopened = _query(stack)
    assert reopened.get_pending_review(review_id=review_ref.resource_id) == review
    with pytest.raises(TypeError, match="MemoryReviewStatus"):
        query.list_pending_reviews(status="pending")
    with pytest.raises(SQLiteCuratorQueryV2Error, match="scope"):
        query.get_pending_review(review_id="memory-review-foreign")

    with sqlite3.connect(stack["database"]) as connection:
        connection.execute(
            """
            UPDATE memory_review_proposals SET review_sha256 = ?
            WHERE review_id = ?
            """,
            ("0" * 64, review_ref.resource_id),
        )
    with pytest.raises(SQLiteCuratorQueryV2IntegrityError):
        query.get_pending_review(review_id=review_ref.resource_id)


def test_pending_review_rejects_stale_target_revision(tmp_path: Path) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, existing, _ = _seed_pending_review(stack)
    query = _query(stack)
    stack["workspace"].write_markdown(
        entry_ref=ResourceRef(
            "memory", existing.entry_id, existing.revision, "space-chat-a"
        ),
        path=existing.path,
        description="Target changed after proposal",
        content="newer target",
        expected_space_revision=2,
        source_refs=(stack["source_a"],),
        operation_id="mutate-review-target",
    )

    with pytest.raises(
        SQLiteCuratorQueryV2IntegrityError,
        match="target_revision_changed",
    ):
        query.get_pending_review(review_id=review_ref.resource_id)


def test_nested_review_diff_binding_is_canonical_and_restart_safe(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, candidate, _, completed = _seed_pending_review(stack)
    expected = completed.candidates[0].to_dict()["review_diff"]
    canonical = json.dumps(
        expected,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    with sqlite3.connect(stack["database"]) as connection:
        row = connection.execute(
            """
            SELECT review_diff_json FROM candidate_bindings
            WHERE candidate_id = ? AND binding_revision = ?
            """,
            (
                candidate.candidate_ref.resource_id,
                completed.candidates[0].binding_revision,
            ),
        ).fetchone()
    assert row is not None
    assert bytes(row[0]) == canonical

    reopened = _query(stack)
    assert reopened.get_candidate(
        candidate_id=candidate.candidate_ref.resource_id
    ) == completed.candidates[0]
    assert reopened.get_pending_review(
        review_id=review_ref.resource_id
    ).review_diff == completed.candidates[0].review_diff


def test_prepared_review_is_hidden_until_candidate_transition_commits(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review, candidate, claimed, capabilities = _seed_prepared_review(stack)

    restarted_before_completion = _query(stack)
    assert restarted_before_completion.list_pending_reviews() == ()
    with pytest.raises(
        SQLiteCuratorQueryV2Error,
        match="memory_review_not_published",
    ):
        restarted_before_completion.get_pending_review(
            review_id=review["result_ref"].resource_id
        )

    completed = stack["repository"].reconcile_and_complete(
        job=claimed,
        resolutions=(
            CandidateResolution(
                candidate_ref=candidate.candidate_ref,
                target_space_id="space-chat-a",
                outcome=CandidateOutcome.AWAITING_USER,
                result_ref=review["result_ref"],
                review_diff=review["review_diff"],
            ),
        ),
        mutation_guard=capabilities.mutation_guard,
        operation=OperationRef(
            "complete-prepared-review",
            hashlib.sha256(b"complete-prepared-review").hexdigest(),
        ),
        now_ms=stack["clock"](),
    )
    assert completed.status is ConsolidationJobStatus.COMPLETED

    restarted_after_completion = _query(stack)
    listed = restarted_after_completion.list_pending_reviews()
    assert len(listed) == 1
    assert listed[0].binding_revision == claimed.candidates[0].binding_revision + 1
    assert listed[0].review_ref == review["result_ref"]


@pytest.mark.parametrize(
    ("transition", "expected_job_status", "expected_candidate_status"),
    (
        (
            "retry",
            ConsolidationJobStatus.PENDING,
            CandidateStatus.QUEUED,
        ),
        (
            "fail",
            ConsolidationJobStatus.FAILED,
            CandidateStatus.ISOLATED,
        ),
        (
            "cancel",
            ConsolidationJobStatus.CANCELLED,
            CandidateStatus.ISOLATED,
        ),
    ),
)
def test_prepared_review_is_hidden_after_normal_job_successor(
    tmp_path: Path,
    transition: str,
    expected_job_status: ConsolidationJobStatus,
    expected_candidate_status: CandidateStatus,
) -> None:
    stack = _open_stack(tmp_path)
    review, _, claimed, _ = _seed_prepared_review(stack)
    operation_id = f"prepared-review-{transition}"
    operation = OperationRef(
        operation_id,
        hashlib.sha256(operation_id.encode("utf-8")).hexdigest(),
    )
    if transition == "cancel":
        successor = stack["repository"].cancel(
            job=claimed,
            reason="prepared_review_cancelled",
            operation=operation,
            now_ms=stack["clock"](),
        )
    else:
        successor = stack["repository"].fail(
            job=claimed,
            error_code="prepared_review_worker_failed",
            retry_at_ms=(
                stack["clock"]() + 1_000
                if transition == "retry"
                else stack["clock"]()
            ),
            operation=operation,
            now_ms=stack["clock"](),
        )

    assert successor.status is expected_job_status
    assert successor.candidates[0].outcome is expected_candidate_status
    restarted = _query(stack)
    assert restarted.list_pending_reviews() == ()
    with pytest.raises(
        SQLiteCuratorQueryV2Error,
        match="memory_review_not_published",
    ):
        restarted.get_pending_review(
            review_id=review["result_ref"].resource_id
        )


def test_unpublished_review_does_not_starve_limited_pending_page(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    published_ref, _, _, _ = _seed_pending_review(stack)
    stack["clock"].now_ms = 2_000
    binding = _binding(
        session_id="session-newer-prepared",
        attempt_id="attempt-newer-prepared",
        run_id="run-newer-prepared",
    )
    target = stack["workspace"].write_markdown(
        path="/decisions/newer-prepared.md",
        description="Newer prepared target",
        content="old policy",
        expected_space_revision=2,
        source_refs=(stack["source_b"],),
        operation_id="seed-newer-prepared-target",
    )
    candidate = _propose(
        stack,
        binding,
        source_ref=stack["source_b"],
        operation_id="candidate-newer-prepared",
        path=target.path,
        content=b"new policy",
    )
    claimed = _enqueue_and_claim(stack, binding)
    capabilities = _capabilities(stack, claimed)
    prepared = capabilities.consolidation.propose_review(
        job_id=claimed.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=claimed.candidates[0].binding_revision,
        target_entry_id=target.entry_id,
        expected_target_revision=target.revision,
        mode="overwrite",
        mutation_guard=capabilities.mutation_guard,
        operation_id="propose-newer-prepared-review",
    )

    restarted = _query(stack)
    assert restarted.get_pending_review(
        review_id=published_ref.resource_id
    ).review_ref == published_ref
    with pytest.raises(
        SQLiteCuratorQueryV2Error,
        match="memory_review_not_published",
    ):
        restarted.get_pending_review(
            review_id=prepared["result_ref"].resource_id
        )
    assert tuple(
        review.review_ref for review in restarted.list_pending_reviews(limit=1)
    ) == (published_ref,)


def test_published_review_content_is_paginated_verified_and_restart_safe(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, candidate, _, _ = _seed_pending_review(stack)
    query = _query(stack)
    diff_ref = _review_content_ref(review_ref, "diff")
    proposed_ref = _review_content_ref(review_ref, "proposed")
    expected_diff = json.dumps(
        query.get_pending_review(
            review_id=review_ref.resource_id
        ).to_dict()["review_diff"],
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    first = query.read_review_content(ref=diff_ref, offset=2, limit=7)
    assert first.ref == diff_ref
    assert first.media_type == "application/json"
    assert first.data == expected_diff[2:9]
    assert first.offset == 2
    assert first.total_bytes == len(expected_diff)
    assert first.sha256 == hashlib.sha256(expected_diff).hexdigest()

    reopened = _query(stack)
    proposed = reopened.read_review_content(
        ref=proposed_ref,
        offset=4,
        limit=128 * 1024,
    )
    assert proposed.ref == proposed_ref
    assert proposed.media_type == "text/markdown"
    assert proposed.data == b"policy"
    assert proposed.total_bytes == len(b"new policy")
    assert proposed.sha256 == candidate.content_sha256

    eof = reopened.read_review_content(
        ref=proposed_ref,
        offset=len(b"new policy"),
        limit=1,
    )
    assert eof.data == b""
    assert eof.next_offset is None
    with pytest.raises(ValueError, match="between 1 and 131072"):
        reopened.read_review_content(
            ref=diff_ref,
            limit=128 * 1024 + 1,
        )
    with pytest.raises(ValueError, match="offset exceeds"):
        reopened.read_review_content(
            ref=proposed_ref,
            offset=len(b"new policy") + 1,
        )
    with pytest.raises(
        SQLiteCuratorQueryV2Error,
        match="memory_review_content_not_found",
    ):
        reopened.read_review_content(
            ref=ResourceRef(
                "memory_review_content",
                review_ref.resource_id,
                1,
                "unknown",
            )
        )


@pytest.mark.parametrize("fragment", ("diff", "proposed"))
def test_prepared_review_content_is_not_readable_before_publication(
    tmp_path: Path,
    fragment: str,
) -> None:
    stack = _open_stack(tmp_path)
    review, _, _, _ = _seed_prepared_review(stack)

    with pytest.raises(
        SQLiteCuratorQueryV2Error,
        match="memory_review_not_published",
    ):
        _query(stack).read_review_content(
            ref=_review_content_ref(review["result_ref"], fragment)
        )


def test_metadata_only_review_has_no_proposed_content(tmp_path: Path) -> None:
    stack = _open_stack(tmp_path)
    binding = _binding()
    existing = stack["workspace"].create_link(
        path="/decisions/reference.link",
        description="Existing reference",
        url="https://example.com/old",
        expected_space_revision=1,
        source_refs=(stack["source_a"],),
        operation_id="seed-existing-link",
    )
    candidate = stack["repository"].bind_candidate_proposals(
        binding=binding
    ).propose(
        request=CandidateProposalRequest(
            path=existing.path,
            description="Updated durable reference",
            kind="link",
            content=None,
            media_type="",
            url="https://example.com/new",
            source_refs=(stack["source_a"],),
            rationale="Preserve the confirmed link",
            confidence=0.98,
            sensitivity="normal",
            operation_id="candidate-link-review",
        )
    )
    claimed = _enqueue_and_claim(stack, binding)
    capabilities = _capabilities(stack, claimed)
    review = capabilities.consolidation.propose_review(
        job_id=claimed.job_id,
        candidate_ref=candidate.candidate_ref,
        expected_binding_revision=claimed.candidates[0].binding_revision,
        target_entry_id=existing.entry_id,
        expected_target_revision=existing.revision,
        mode="overwrite",
        mutation_guard=capabilities.mutation_guard,
        operation_id="propose-link-review",
    )
    completed = stack["repository"].reconcile_and_complete(
        job=claimed,
        resolutions=(
            CandidateResolution(
                candidate_ref=candidate.candidate_ref,
                target_space_id="space-chat-a",
                outcome=CandidateOutcome.AWAITING_USER,
                result_ref=review["result_ref"],
                review_diff=review["review_diff"],
            ),
        ),
        mutation_guard=capabilities.mutation_guard,
        operation=OperationRef(
            "complete-link-review",
            hashlib.sha256(b"complete-link-review").hexdigest(),
        ),
        now_ms=stack["clock"](),
    )
    assert completed.status is ConsolidationJobStatus.COMPLETED

    with pytest.raises(
        SQLiteCuratorQueryV2Error,
        match="memory_review_content_not_found",
    ):
        _query(stack).read_review_content(
            ref=_review_content_ref(review["result_ref"], "proposed")
        )


def test_published_review_content_survives_decision_and_target_update(
    tmp_path: Path,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, existing, completed = _seed_pending_review(stack)
    published = completed.candidates[0]
    applied = published.with_outcome(
        CandidateStatus.APPLIED,
        result_ref=ResourceRef(
            "memory",
            existing.entry_id,
            existing.revision,
            "space-chat-a",
        ),
    )
    with stack["repository"]._store._transaction(
        immediate=True
    ) as connection:
        stack["repository"]._write_candidate_transition(
            connection,
            before=published,
            after=applied,
            job_id=completed.job_id,
            operation_id="simulate-review-decision",
            now_ms=stack["clock"](),
        )
    stack["workspace"].write_markdown(
        entry_ref=ResourceRef(
            "memory",
            existing.entry_id,
            existing.revision,
            "space-chat-a",
        ),
        path=existing.path,
        description="Target updated after review decision",
        content="accepted policy",
        expected_space_revision=2,
        source_refs=(stack["source_a"],),
        operation_id="update-target-after-review",
    )

    reopened = _query(stack)
    diff = reopened.read_review_content(
        ref=_review_content_ref(review_ref, "diff")
    )
    proposed = reopened.read_review_content(
        ref=_review_content_ref(review_ref, "proposed")
    )
    assert json.loads(diff.data)["requires_user_confirmation"] is True
    assert proposed.data == b"new policy"


@pytest.mark.parametrize(
    "corruption",
    (
        "review_hash",
        "publication_binding",
        "publication_binding_missing",
        "job_hash",
        "object",
    ),
)
def test_review_content_corruption_fails_closed(
    tmp_path: Path,
    corruption: str,
) -> None:
    stack = _open_stack(tmp_path)
    review_ref, candidate, _, completed = _seed_pending_review(stack)
    fragment = "proposed" if corruption == "object" else "diff"
    if corruption == "review_hash":
        with sqlite3.connect(stack["database"]) as connection:
            connection.execute(
                """
                UPDATE memory_review_proposals SET review_sha256 = ?
                WHERE review_id = ?
                """,
                ("0" * 64, review_ref.resource_id),
            )
    elif corruption == "publication_binding":
        with sqlite3.connect(stack["database"]) as connection:
            connection.execute(
                """
                UPDATE candidate_bindings SET review_diff_json = ?
                WHERE candidate_id = ? AND binding_revision = ?
                """,
                (
                    b"{}",
                    candidate.candidate_ref.resource_id,
                    completed.candidates[0].binding_revision,
                ),
            )
    elif corruption == "publication_binding_missing":
        with sqlite3.connect(stack["database"]) as connection:
            connection.execute(
                """
                DELETE FROM candidate_bindings
                WHERE candidate_id = ? AND binding_revision = ?
                """,
                (
                    candidate.candidate_ref.resource_id,
                    completed.candidates[0].binding_revision,
                ),
            )
    elif corruption == "job_hash":
        with sqlite3.connect(stack["database"]) as connection:
            connection.execute(
                """
                UPDATE consolidation_job_revisions SET job_sha256 = ?
                WHERE job_id = ? AND revision = ?
                """,
                ("0" * 64, completed.job_id, completed.revision),
            )
    else:
        (stack["objects"] / candidate.content_sha256).write_bytes(b"tampered")

    with pytest.raises(SQLiteCuratorQueryV2IntegrityError):
        _query(stack).read_review_content(
            ref=_review_content_ref(review_ref, fragment)
        )


def test_review_content_scope_mismatch_is_not_found(tmp_path: Path) -> None:
    stack = _open_stack(tmp_path)
    review_ref, _, _, _ = _seed_pending_review(stack)
    with sqlite3.connect(stack["database"]) as connection:
        connection.execute(
            """
            UPDATE memory_review_proposals SET target_space_id = ?
            WHERE review_id = ?
            """,
            ("space-foreign", review_ref.resource_id),
        )

    with pytest.raises(
        SQLiteCuratorQueryV2Error,
        match="memory_review_content_not_found",
    ):
        _query(stack).read_review_content(
            ref=_review_content_ref(review_ref, "diff")
        )
