from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest

from unchain.journal import OperationRef, ResourceRef
from unchain.memory.curator import (
    CandidateOutcome,
    CandidateResolution,
    CandidateStatus,
    ConsolidationJobStatus,
    CurationConflictError,
    CurationRepositoryError,
    CuratorCoordinator,
    CuratorLeaseFence,
    CuratorRunResult,
    EnqueueDisposition,
    RootRunCompletion,
    RunCaptureStatus,
    SourceRunStatus,
)
from unchain.memory.toolkit import CandidateProposalRequest, MemoryToolkitRunBinding
from unchain.persistence.sqlite_curator_v2 import SQLiteCuratorV2Store
from unchain.persistence.sqlite_v2 import SQLiteContextV2Store


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


def _completion(
    binding: MemoryToolkitRunBinding,
    **changes,
) -> RootRunCompletion:
    values = {
        "session_id": binding.session_id,
        "attempt_id": binding.attempt_id,
        "run_id": binding.run_id,
        "is_root_run": True,
        "run_status": SourceRunStatus.COMPLETED,
        "capture_status": RunCaptureStatus.COMPLETE,
    }
    values.update(changes)
    return RootRunCompletion(**values)


def _proposal(
    binding: MemoryToolkitRunBinding,
    *,
    operation_id: str = "candidate-proposal-a",
    path: str = "/notes/Memory V2.md",
    content: bytes = b"full candidate body",
    description: str = "Durable candidate for the Memory V2 design",
) -> CandidateProposalRequest:
    return CandidateProposalRequest(
        path=path,
        description=description,
        kind="markdown",
        content=content,
        media_type="text/markdown",
        url="",
        source_refs=(ResourceRef("context_event", f"event-{binding.run_id}", 1),),
        rationale="Keep the confirmed architecture decision",
        confidence=0.91,
        sensitivity="normal",
        operation_id=operation_id,
    )


def _build(
    root: Path,
    *,
    now: list[int] | None = None,
    binding_id: str = "binding-chat-a",
    owner_chat_id: str = "chat-a",
    target_space_id: str = "space-chat-a",
):
    observed = now if now is not None else [1_000]
    store = SQLiteCuratorV2Store(
        database_path=root / "context_v2.sqlite3",
        object_directory=root / "objects",
        clock_ms=lambda: observed[0],
    )
    repository = store.bind_curation(
        binding_id=binding_id,
        owner_chat_id=owner_chat_id,
        target_space_id=target_space_id,
    )
    return store, repository, observed


def _propose(repository, binding: MemoryToolkitRunBinding, **changes):
    capability = repository.bind_candidate_proposals(binding=binding)
    return capability.propose(request=_proposal(binding, **changes))


def _enqueue_and_claim(repository, binding, *, now_ms=1_000):
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: now_ms)
    enqueued = coordinator.enqueue(_completion(binding))
    assert enqueued.disposition is EnqueueDisposition.ENQUEUED
    claimed = coordinator.claim_next(
        worker_id="worker-a",
        lease_ms=1_000,
        operation_id=f"claim-{binding.run_id}",
    )
    assert claimed is not None
    return coordinator, claimed


def _applied_resolution(candidate):
    return CandidateResolution(
        candidate_ref=candidate.candidate_ref,
        target_space_id=candidate.target_space_id,
        outcome=CandidateOutcome.APPLIED,
        result_ref=ResourceRef(
            "memory",
            f"entry-{candidate.candidate_ref.resource_id}",
            1,
            candidate.target_space_id,
        ),
    )


class _CoordinatorRunnerToolkit:
    def __init__(self, repository, job) -> None:
        self.binding_id = repository.binding_id
        self.job_id = job.job_id
        self.candidate_refs = tuple(item.candidate_ref for item in job.candidates)
        self.lease_fence = CuratorLeaseFence.from_job(repository.binding_id, job)
        self.mutation_guard = repository.bind_mutation_guard(job=job)


class _CoordinatorRunner:
    def __init__(self, repository, job) -> None:
        self.binding_id = repository.binding_id
        self.job_id = job.job_id
        self.candidate_refs = tuple(item.candidate_ref for item in job.candidates)
        self.lease_fence = CuratorLeaseFence.from_job(repository.binding_id, job)
        self.toolkit = _CoordinatorRunnerToolkit(repository, job)

    def run(self, request, *, mutation_guard):
        assert request.lease_fence == self.lease_fence
        assert mutation_guard.fence == self.lease_fence
        return CuratorRunResult(
            tuple(_applied_resolution(item) for item in request.job.candidates)
        )


def test_candidate_proposal_is_immediately_cas_backed_and_restart_safe(
    tmp_path: Path,
) -> None:
    database = tmp_path / "context_v2.sqlite3"
    objects = tmp_path / "objects"
    SQLiteContextV2Store(database_path=database, object_directory=objects)
    _, repository, _ = _build(tmp_path)
    binding = _binding()
    payload = b"complete candidate content\n" * 400

    candidate = _propose(repository, binding, content=payload)

    assert candidate.outcome is CandidateStatus.PENDING
    assert candidate.source_agent_run_id == binding.run_id
    assert candidate.byte_length == len(payload)
    assert candidate.content_sha256 == hashlib.sha256(payload).hexdigest()
    assert (
        repository.read_candidate_content(
            ref=candidate.candidate_ref,
            offset=17,
            limit=113,
        ).data
        == payload[17:130]
    )
    assert (objects / candidate.content_sha256).read_bytes() == payload
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {
        "candidates",
        "candidate_revisions",
        "candidate_bindings",
        "consolidation_jobs",
        "consolidation_job_revisions",
        "curator_operation_receipts",
        "objects",
    } <= tables

    _, reopened, _ = _build(tmp_path)
    assert reopened.read_candidate(ref=candidate.candidate_ref) == candidate
    assert (
        reopened.read_candidate_content(
            ref=candidate.candidate_ref,
            offset=0,
            limit=len(payload),
        ).data
        == payload
    )
    with pytest.raises(TypeError, match="ResourceRef"):
        reopened.read_candidate_content(ref="foreign", offset=0, limit=1)


def test_legacy_run_scopes_migrate_with_the_source_run_as_root_lineage(
    tmp_path: Path,
) -> None:
    database = tmp_path / "context_v2.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            PRAGMA foreign_keys = ON;
            CREATE TABLE curator_v2_schema (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO curator_v2_schema(version) VALUES (1);
            CREATE TABLE curation_scopes (
                binding_id TEXT PRIMARY KEY,
                owner_chat_id TEXT NOT NULL,
                target_space_id TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0)
            );
            CREATE TABLE curation_run_scopes (
                binding_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL CHECK(created_at_ms >= 0),
                PRIMARY KEY (binding_id, session_id, attempt_id, run_id),
                FOREIGN KEY (binding_id) REFERENCES curation_scopes(binding_id)
            );
            INSERT INTO curation_scopes(
                binding_id, owner_chat_id, target_space_id, created_at_ms
            ) VALUES ('binding-chat-a', 'chat-a', 'space-chat-a', 900);
            INSERT INTO curation_run_scopes(
                binding_id, session_id, attempt_id, run_id, created_at_ms
            ) VALUES ('binding-chat-a', 'session-a', 'attempt-a', 'run-a', 901);
            """
        )

    SQLiteCuratorV2Store(
        database_path=database,
        object_directory=tmp_path / "objects",
    )
    SQLiteCuratorV2Store(
        database_path=database,
        object_directory=tmp_path / "objects",
    )

    with sqlite3.connect(database) as connection:
        columns = {
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info(curation_run_scopes)")
        }
        row = connection.execute(
            """
            SELECT session_id, attempt_id, run_id, root_run_id, created_at_ms
            FROM curation_run_scopes
            """
        ).fetchone()
        index_names = {
            item[1]
            for item in connection.execute("PRAGMA index_list(curation_run_scopes)")
        }
    assert columns["root_run_id"] == 1
    assert row == ("session-a", "attempt-a", "run-a", "run-a", 901)
    assert "idx_curation_run_scopes_root" in index_names


def test_root_completion_aggregates_graph_step_candidates_with_true_provenance(
    tmp_path: Path,
) -> None:
    _, repository, _ = _build(tmp_path)
    root = _binding(attempt_id="coordinator-attempt", run_id="root-run")
    first_step = _binding(attempt_id="step-attempt-a", run_id="graph-step-a")
    second_step = _binding(attempt_id="step-attempt-b", run_id="graph-step-b")
    foreign_step = _binding(attempt_id="step-attempt-c", run_id="graph-step-c")
    repository.bind_candidate_proposals(
        binding=root,
        root_run_id=root.run_id,
    )
    first = repository.bind_candidate_proposals(
        binding=first_step,
        root_run_id=root.run_id,
    ).propose(
        request=_proposal(
            first_step,
            operation_id="graph-candidate-a",
            path="/graph/step-a.md",
        )
    )
    second = repository.bind_candidate_proposals(
        binding=second_step,
        root_run_id=root.run_id,
    ).propose(
        request=_proposal(
            second_step,
            operation_id="graph-candidate-b",
            path="/graph/step-b.md",
        )
    )
    foreign = repository.bind_candidate_proposals(
        binding=foreign_step,
        root_run_id="foreign-root-run",
    ).propose(
        request=_proposal(
            foreign_step,
            operation_id="graph-candidate-c",
            path="/graph/foreign.md",
        )
    )

    pending = repository.list_pending_candidates(
        completion=_completion(root),
        limit=20,
    )
    assert {item.candidate_ref for item in pending} == {
        first.candidate_ref,
        second.candidate_ref,
    }
    assert {item.source_agent_run_id for item in pending} == {
        first_step.run_id,
        second_step.run_id,
    }
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1_000)
    result = coordinator.enqueue(_completion(root))
    assert result.disposition is EnqueueDisposition.ENQUEUED
    assert {item.source_agent_run_id for item in result.job.candidates} == {
        first_step.run_id,
        second_step.run_id,
    }
    assert repository.read_candidate(ref=foreign.candidate_ref).outcome is CandidateStatus.PENDING

    with pytest.raises(
        CurationRepositoryError,
        match="candidate_root_run_scope_mismatch",
    ):
        repository.bind_candidate_proposals(
            binding=first_step,
            root_run_id="drifted-root-run",
        )


def test_failed_root_completion_isolates_all_graph_step_candidates_only(
    tmp_path: Path,
) -> None:
    _, repository, _ = _build(tmp_path)
    root = _binding(attempt_id="coordinator-attempt", run_id="root-run")
    first_step = _binding(attempt_id="step-attempt-a", run_id="graph-step-a")
    second_step = _binding(attempt_id="step-attempt-b", run_id="graph-step-b")
    foreign_step = _binding(attempt_id="step-attempt-c", run_id="graph-step-c")
    repository.bind_candidate_proposals(binding=root, root_run_id=root.run_id)
    candidates = tuple(
        repository.bind_candidate_proposals(
            binding=step,
            root_run_id=root.run_id,
        ).propose(
            request=_proposal(
                step,
                operation_id=f"isolate-{step.run_id}",
                path=f"/graph/{step.run_id}.md",
            )
        )
        for step in (first_step, second_step)
    )
    foreign = repository.bind_candidate_proposals(
        binding=foreign_step,
        root_run_id="foreign-root-run",
    ).propose(
        request=_proposal(
            foreign_step,
            operation_id="isolate-foreign",
            path="/graph/foreign.md",
        )
    )

    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1_000)
    result = coordinator.enqueue(
        _completion(root, run_status=SourceRunStatus.FAILED)
    )

    assert result.disposition is EnqueueDisposition.ISOLATED
    assert result.isolated_candidate_count == 2
    assert {
        repository.read_candidate(ref=item.candidate_ref).outcome
        for item in candidates
    } == {CandidateStatus.ISOLATED}
    assert repository.read_candidate(ref=foreign.candidate_ref).outcome is CandidateStatus.PENDING


def test_candidate_operation_replay_drift_and_scope_are_enforced(
    tmp_path: Path,
) -> None:
    store, repository, _ = _build(tmp_path)
    binding = _binding()
    capability = repository.bind_candidate_proposals(binding=binding)
    request = _proposal(binding)

    first = capability.propose(request=request)
    assert capability.propose(request=request) == first
    with pytest.raises(CurationConflictError, match="operation"):
        capability.propose(
            request=_proposal(binding, description="operation payload drift")
        )
    with pytest.raises(CurationRepositoryError, match="scope"):
        repository.bind_candidate_proposals(
            binding=_binding(binding_id="foreign-binding")
        )
    with pytest.raises(CurationRepositoryError, match="scope"):
        store.bind_curation(
            binding_id=repository.binding_id,
            owner_chat_id="foreign-chat",
            target_space_id="space-chat-a",
        )


def test_operation_ids_are_namespaced_by_binding(tmp_path: Path) -> None:
    store, first_repository, _ = _build(tmp_path)
    first_binding = _binding()
    first = _propose(
        first_repository,
        first_binding,
        operation_id="shared-operation-id",
    )
    second_repository = store.bind_curation(
        binding_id="binding-chat-b",
        owner_chat_id="chat-b",
        target_space_id="space-chat-b",
    )
    second_binding = _binding(
        binding_id="binding-chat-b",
        session_id=first_binding.session_id,
        attempt_id=first_binding.attempt_id,
        run_id=first_binding.run_id,
    )
    second = _propose(
        second_repository,
        second_binding,
        operation_id="shared-operation-id",
    )

    assert second.candidate_ref != first.candidate_ref
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        assert (
            connection.execute(
                """
            SELECT COUNT(*) FROM curator_operation_receipts
            WHERE operation_id = ?
            """,
                ("shared-operation-id",),
            ).fetchone()[0]
            == 2
        )


@pytest.mark.parametrize(
    "changes",
    (
        {"kind": "markdown", "content": None, "media_type": "text/markdown"},
        {"kind": "folder", "content": b"not allowed", "media_type": ""},
        {"kind": "link", "content": None, "media_type": "", "url": ""},
    ),
)
def test_candidate_proposal_rejects_non_durable_payload_shapes(
    tmp_path: Path,
    changes,
) -> None:
    _, repository, _ = _build(tmp_path)
    binding = _binding()
    capability = repository.bind_candidate_proposals(binding=binding)
    baseline = _proposal(binding)
    values = {
        "path": baseline.path,
        "description": baseline.description,
        "kind": baseline.kind,
        "content": baseline.content,
        "media_type": baseline.media_type,
        "url": baseline.url,
        "source_refs": baseline.source_refs,
        "rationale": baseline.rationale,
        "confidence": baseline.confidence,
        "sensitivity": baseline.sensitivity,
        "operation_id": baseline.operation_id,
    }
    values.update(changes)

    with pytest.raises(CurationRepositoryError, match="payload"):
        capability.propose(request=CandidateProposalRequest(**values))

    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0


def test_no_candidate_is_noop_and_duplicate_root_callback_creates_one_job(
    tmp_path: Path,
) -> None:
    _, repository, _ = _build(tmp_path)
    empty_binding = _binding(run_id="run-empty", attempt_id="attempt-empty")
    repository.bind_candidate_proposals(binding=empty_binding)
    empty = CuratorCoordinator(repository, clock_ms=lambda: 1_000)
    assert (
        empty.enqueue(_completion(empty_binding)).disposition
        is EnqueueDisposition.NO_OP
    )

    binding = _binding()
    _propose(repository, binding)
    coordinator = CuratorCoordinator(repository, clock_ms=lambda: 1_000)
    first = coordinator.enqueue(_completion(binding))
    second = coordinator.enqueue(_completion(binding))

    assert first.disposition is EnqueueDisposition.ENQUEUED
    assert second.disposition is EnqueueDisposition.REPLAYED
    assert second.job == first.job
    with sqlite3.connect(tmp_path / "context_v2.sqlite3") as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM consolidation_jobs").fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    ("changes", "reason"),
    (
        ({"run_status": SourceRunStatus.FAILED}, "source_run_failed"),
        ({"run_status": SourceRunStatus.CANCELLED}, "source_run_cancelled"),
        ({"capture_status": RunCaptureStatus.PARTIAL}, "source_capture_partial"),
    ),
)
def test_failed_cancelled_or_partial_sources_are_isolated(
    tmp_path: Path,
    changes,
    reason: str,
) -> None:
    _, repository, _ = _build(tmp_path)
    binding = _binding()
    candidate = _propose(repository, binding)

    result = CuratorCoordinator(repository).enqueue(_completion(binding, **changes))

    assert result.disposition is EnqueueDisposition.ISOLATED
    assert result.reason == reason
    assert result.isolated_candidate_count == 1
    isolated = repository.read_candidate(ref=candidate.candidate_ref)
    assert isolated.outcome is CandidateStatus.ISOLATED
    assert isolated.error_code == reason
    assert (
        repository.find_job_by_trigger(trigger_key=_completion(binding).trigger_key)
        is None
    )


def test_lease_expiry_can_be_reclaimed_after_restart_and_old_guard_is_stale(
    tmp_path: Path,
) -> None:
    _, repository, now = _build(tmp_path)
    binding = _binding()
    _propose(repository, binding)
    first = CuratorCoordinator(repository, clock_ms=lambda: now[0])
    first.enqueue(_completion(binding))
    claimed = first.claim_next(
        worker_id="worker-a",
        lease_ms=1_000,
        operation_id="claim-first",
    )
    guard = repository.bind_mutation_guard(job=claimed)

    now[0] = 1_999
    _, before_expiry, _ = _build(tmp_path, now=now)
    assert (
        CuratorCoordinator(before_expiry, clock_ms=lambda: now[0]).claim_next(
            worker_id="worker-b",
            lease_ms=1_000,
            operation_id="claim-before-expiry",
        )
        is None
    )

    now[0] = 2_000
    _, after_expiry, _ = _build(tmp_path, now=now)
    reclaimed = CuratorCoordinator(after_expiry, clock_ms=lambda: now[0]).claim_next(
        worker_id="worker-b",
        lease_ms=1_000,
        operation_id="claim-after-expiry",
    )

    assert reclaimed.job_id == claimed.job_id
    assert reclaimed.revision == claimed.revision + 1
    assert reclaimed.lease.owner == "worker-b"
    assert reclaimed.lease.token != claimed.lease.token
    with pytest.raises(CurationConflictError, match="lease"):
        guard.assert_active()


def test_reconcile_and_complete_is_atomic_and_rejects_stale_or_partial_results(
    tmp_path: Path,
) -> None:
    _, repository, _ = _build(tmp_path)
    binding = _binding()
    first_candidate = _propose(repository, binding, operation_id="candidate-first")
    second_candidate = _propose(
        repository,
        binding,
        operation_id="candidate-second",
        path="/notes/Second.md",
        description="Second durable candidate in the same job",
    )
    _, claimed = _enqueue_and_claim(repository, binding)
    guard = repository.bind_mutation_guard(job=claimed)
    incomplete = (_applied_resolution(claimed.candidates[0]),)

    with pytest.raises(CurationConflictError, match="resolution"):
        repository.reconcile_and_complete(
            job=claimed,
            resolutions=incomplete,
            mutation_guard=guard,
            operation=OperationRef("reconcile-incomplete", "a" * 64),
            now_ms=1_100,
        )

    still_leased = repository.read_job(job_id=claimed.job_id)
    assert still_leased == claimed
    assert (
        repository.read_candidate(ref=first_candidate.candidate_ref).outcome
        is CandidateStatus.PROCESSING
    )
    assert (
        repository.read_candidate(ref=second_candidate.candidate_ref).outcome
        is CandidateStatus.PROCESSING
    )

    resolutions = tuple(_applied_resolution(item) for item in claimed.candidates)
    operation = OperationRef("reconcile-complete", "b" * 64)
    completed = repository.reconcile_and_complete(
        job=claimed,
        resolutions=resolutions,
        mutation_guard=guard,
        operation=operation,
        now_ms=1_100,
    )

    assert completed.status is ConsolidationJobStatus.COMPLETED
    assert (
        repository.reconcile_and_complete(
            job=claimed,
            resolutions=resolutions,
            mutation_guard=guard,
            operation=operation,
            now_ms=1_100,
        )
        == completed
    )
    with pytest.raises(CurationConflictError, match="lease"):
        guard.assert_active()

    _, reopened, _ = _build(tmp_path)
    assert reopened.read_job(job_id=claimed.job_id) == completed
    assert all(
        reopened.read_candidate(ref=item.candidate_ref).outcome
        is CandidateStatus.APPLIED
        for item in completed.candidates
    )


def test_real_repository_satisfies_the_coordinator_process_contract(
    tmp_path: Path,
) -> None:
    _, repository, _ = _build(tmp_path)
    binding = _binding()
    _propose(repository, binding)
    coordinator, claimed = _enqueue_and_claim(repository, binding)

    result = coordinator.process_claimed(
        claimed,
        runner=_CoordinatorRunner(repository, claimed),
    )

    assert result.job.status is ConsolidationJobStatus.COMPLETED
    _, reopened, _ = _build(tmp_path)
    assert reopened.read_job(job_id=claimed.job_id) == result.job


def test_retry_terminal_failure_and_cancel_survive_restart(tmp_path: Path) -> None:
    _, repository, now = _build(tmp_path)
    binding = _binding()
    _propose(repository, binding)
    _, claimed = _enqueue_and_claim(repository, binding)
    retried = repository.fail(
        job=claimed,
        error_code="provider_unavailable",
        retry_at_ms=3_000,
        operation=OperationRef("retry-job", "c" * 64),
        now_ms=1_100,
    )
    assert retried.status is ConsolidationJobStatus.PENDING
    assert retried.next_attempt_at_ms == 3_000

    now[0] = 3_000
    reclaimed = CuratorCoordinator(repository, clock_ms=lambda: now[0]).claim_next(
        worker_id="worker-b",
        lease_ms=1_000,
        operation_id="claim-retry",
    )
    failed = repository.fail(
        job=reclaimed,
        error_code="terminal_model_failure",
        retry_at_ms=0,
        operation=OperationRef("fail-job", "d" * 64),
        now_ms=3_100,
    )
    assert failed.status is ConsolidationJobStatus.FAILED

    cancel_binding = _binding(run_id="run-cancel", attempt_id="attempt-cancel")
    _propose(
        repository,
        cancel_binding,
        operation_id="candidate-cancel",
        path="/notes/Cancel.md",
        description="Candidate whose pending job is cancelled",
    )
    cancellation = CuratorCoordinator(repository, clock_ms=lambda: 3_200).enqueue(
        _completion(cancel_binding)
    )
    cancelled = repository.cancel(
        job=cancellation.job,
        reason="user_cancelled",
        operation=OperationRef("cancel-job", "e" * 64),
        now_ms=3_200,
    )
    assert cancelled.status is ConsolidationJobStatus.CANCELLED

    _, reopened, _ = _build(tmp_path, now=now)
    assert reopened.read_job(job_id=failed.job_id) == failed
    assert reopened.read_job(job_id=cancelled.job_id) == cancelled


def test_concurrent_claim_has_one_winner_and_one_noop(tmp_path: Path) -> None:
    _, repository, _ = _build(tmp_path)
    binding = _binding()
    _propose(repository, binding)
    CuratorCoordinator(repository, clock_ms=lambda: 1_000).enqueue(_completion(binding))
    _, first, _ = _build(tmp_path)
    _, second, _ = _build(tmp_path)
    barrier = threading.Barrier(2)
    outcomes = []
    errors = []

    def claim(repository, worker: str) -> None:
        barrier.wait()
        try:
            outcomes.append(
                CuratorCoordinator(repository, clock_ms=lambda: 1_000).claim_next(
                    worker_id=worker,
                    lease_ms=1_000,
                    operation_id=f"concurrent-{worker}",
                )
            )
        except Exception as exc:  # noqa: BLE001 - asserted below
            errors.append(exc)

    threads = (
        threading.Thread(target=claim, args=(first, "worker-first")),
        threading.Thread(target=claim, args=(second, "worker-second")),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sum(item is not None for item in outcomes) == 1
    assert sum(item is None for item in outcomes) == 1
