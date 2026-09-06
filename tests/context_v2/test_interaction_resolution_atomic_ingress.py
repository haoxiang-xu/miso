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


def test_precondition_rejects_after_concurrent_resolution(tmp_path):
    _, journal, projectors, sinks, _, _ = fixture._bootstrap(tmp_path)
    fixture._request(sinks[fixture.STEP], fixture.INTERACTION_ID, 1)
    fixture._resolve(projectors[fixture.STEP], sinks[fixture.STEP], fixture.INTERACTION_ID)
    with pytest.raises((InteractionAcceptanceConflict, JournalConflictError)):
        _ingress(projectors, sinks).persist(
            _input("something else"), precondition=_precondition(journal)
        )
    assert [e.event_type for e in journal.capture_snapshot().events].count(
        "interaction.resolved"
    ) == 1


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
