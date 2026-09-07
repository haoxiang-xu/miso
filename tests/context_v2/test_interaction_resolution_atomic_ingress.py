"""BC-009: interaction resolution acceptance and its artifact commit atomically."""

import pytest

import test_graph_interaction_resume_checkpoint as fixture
from unchain.context.ingress import ContextInputIngress, HostResolvedInteractionInput
from unchain.context.interaction_acceptance import (
    InteractionAcceptanceConflict,
    assert_interaction_unresolved,
)
from unchain.journal import JournalConflictError
from unchain.persistence.sqlite_v2 import _SQLiteBoundContextV2Repository


def _ingress(projectors, sinks):
    return ContextInputIngress(
        attempt=fixture.STEP,
        projector=projectors[fixture.STEP],
        sink=sinks[fixture.STEP],
    )


def _input(answer):
    return HostResolvedInteractionInput(
        attempt=fixture.STEP,
        interaction_id=fixture.INTERACTION_ID,
        response={"answer": answer},
    )


def _precondition(journal):
    def check(snapshot):
        assert_interaction_unresolved(
            snapshot, attempt=fixture.STEP, interaction_id=fixture.INTERACTION_ID
        )

    return check


def test_interrupted_event_does_not_reserve_the_question(tmp_path, monkeypatch):
    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    original = _SQLiteBoundContextV2Repository._append_with_connection
    interrupted = []

    def explode(self, connection, request):
        if request.event_type == "interaction.resolved":
            interrupted.append(request.event_id)
            raise RuntimeError("interrupted between artifact and event")
        return original(self, connection, request)

    monkeypatch.setattr(_SQLiteBoundContextV2Repository, "_append_with_connection", explode)
    with pytest.raises(RuntimeError, match="interrupted"):
        _ingress(projectors, sinks).persist(_input("vue"), precondition=_precondition(journal))
    assert interrupted
    monkeypatch.setattr(_SQLiteBoundContextV2Repository, "_append_with_connection", original)
    result = _ingress(projectors, sinks).persist(_input("react"), precondition=_precondition(journal))
    assert result.event.payload["interaction_id"] == fixture.INTERACTION_ID
    assert result.duplicate is False


def test_same_identity_competing_answer_is_a_replay_conflict(tmp_path):
    """Two canonical answers share one operation identity: the journal's own
    replay check rejects the second before the precondition is consulted."""

    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    fixture._resolve(projectors[fixture.STEP], sinks[fixture.STEP], fixture.INTERACTION_ID)
    calls = []

    def recording(snapshot):
        calls.append(snapshot.high_water)
        _precondition(journal)(snapshot)

    with pytest.raises(JournalConflictError):
        _ingress(projectors, sinks).persist(_input("something else"), precondition=recording)
    assert calls == [], "replay conflict must fire before the precondition"
    assert [e.event_type for e in journal.capture_snapshot().events].count(
        "interaction.resolved"
    ) == 1


def test_precondition_rejects_after_concurrent_resolution(tmp_path):
    """A resolution landed under a different operation identity (a legacy
    underscore-spelled marker) is invisible to the replay check, so only the
    in-transaction precondition can refuse the answer -- and it must."""

    from unchain.journal import SemanticEventDraft

    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    sinks[fixture.STEP].append_projected(
        SemanticEventDraft(
            event_id="review-legacy-resolution",
            event_type="interaction_resolved",
            attempt=fixture.STEP,
            operation_id="review-legacy-resolution-operation",
            payload={
                "run_id": fixture.STEP.attempt_id,
                "interaction_id": fixture.INTERACTION_ID,
                "kind": "human_input",
                "outcome": "submitted",
            },
        )
    )
    calls = []

    def recording(snapshot):
        calls.append(snapshot.high_water)
        _precondition(journal)(snapshot)

    with pytest.raises(InteractionAcceptanceConflict) as rejected:
        _ingress(projectors, sinks).persist(_input("something else"), precondition=recording)
    assert rejected.value.reason == "already_resolved"
    assert len(calls) == 1, "the precondition must run exactly once, in the transaction"
    events = journal.capture_snapshot().events
    assert [e.event_type for e in events].count("interaction.resolved") == 0
    assert [e.event_type for e in events].count("interaction_resolved") == 1


def test_precondition_rejects_when_no_request_is_pending(tmp_path):
    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    calls = []

    def recording(snapshot):
        calls.append(snapshot.high_water)
        _precondition(journal)(snapshot)

    with pytest.raises(InteractionAcceptanceConflict) as rejected:
        _ingress(projectors, sinks).persist(_input("unasked"), precondition=recording)
    assert rejected.value.reason == "not_pending"
    assert len(calls) == 1
    assert not any(
        e.event_type == "interaction.resolved" for e in journal.capture_snapshot().events
    )


def test_same_answer_replays_without_running_precondition(tmp_path):
    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    first = _ingress(projectors, sinks).persist(_input("vue"), precondition=_precondition(journal))
    calls = []
    again = _ingress(projectors, sinks).persist(_input("vue"), precondition=calls.append)
    assert again.duplicate is True
    assert again.event == first.event
    assert calls == []


def test_legacy_project_then_append_stays_valid_and_replays(tmp_path):
    """Old producer path (eager artifact + separate append) remains accepted."""
    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    draft = projectors[fixture.STEP].project_interaction_resolution(
        interaction_id=fixture.INTERACTION_ID, response={"answer": "vue"}
    )
    legacy = sinks[fixture.STEP].append_projected(draft)
    replay = _ingress(projectors, sinks).persist(_input("vue"), precondition=_precondition(journal))
    assert replay.duplicate is True
    assert replay.event == legacy.event


def test_read_only_journal_reads_back_the_accepted_artifact_without_files(tmp_path):
    from unchain.persistence.sqlite_v2 import open_existing_execution_journal_readonly

    _, journal, projectors, sinks, _, plan = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    accepted = _ingress(projectors, sinks).persist(
        _input("vue"), precondition=_precondition(journal)
    )
    database = tmp_path / "memory_v2" / "context_v2.sqlite3"
    objects = tmp_path / "memory_v2" / "objects"
    reader = open_existing_execution_journal_readonly(
        database_path=database,
        execution_id=fixture.GENERATION.execution_id,
        object_directory=objects,
    )
    from unchain.journal.models import ArtifactRef, ResourceRef

    payload = accepted.event.payload
    artifact = ArtifactRef(
        ref=ResourceRef.from_dict(payload["content_ref"]),
        media_type="application/json",
        byte_length=int(payload["content_bytes"]),
        sha256=str(payload["content_sha256"]),
        preview=str(payload.get("preview") or ""),
    )
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    content = reader.read_artifact_full_verified(artifact=artifact)
    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert after == before, f"read created {after - before}"
    import json

    decoded = json.loads(content.decode("utf-8"))
    assert decoded == {
        "interaction_id": fixture.INTERACTION_ID,
        "response": {"answer": "vue"},
        "submitted_by": "user",
    }
