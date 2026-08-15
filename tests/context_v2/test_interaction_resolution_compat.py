from __future__ import annotations

import hashlib
import json

import pytest

from unchain.journal.interaction_resolution_compat import (
    InteractionResolutionCompatibilityError,
    interaction_resolution_compatibility_record,
    legacy_interaction_resolution_supersession_pairs,
    legacy_interaction_resolution_supersessions,
)


def _payload(
    *,
    ref_id: str = "answer-artifact",
    interaction_id: str = "interaction-1",
) -> dict:
    preview = json.dumps({"answer": "yes"}, separators=(",", ":"))
    content = preview.encode("utf-8")
    return {
        "interaction_id": interaction_id,
        "submitted_by": "ui:test",
        "content_ref": {
            "kind": "artifact",
            "id": ref_id,
            "revision": 1,
        },
        "content_bytes": len(content),
        "content_sha256": hashlib.sha256(content).hexdigest(),
        "preview": preview,
        "preview_truncated": False,
    }


def _record(
    ordinal: int,
    event_type: str,
    *,
    interaction_id: str = "interaction-1",
    payload: dict | None = None,
    resource_ref_id: str | None = None,
):
    bound_payload = {} if payload is None else payload
    refs = (
        ()
        if resource_ref_id is None
        else (
            {
                "kind": "artifact",
                "id": resource_ref_id,
                "revision": 1,
            },
        )
    )
    return interaction_resolution_compatibility_record(
        ordinal=ordinal,
        event_type=event_type,
        interaction_id=interaction_id,
        execution_id="execution-1",
        generation_id="generation-1",
        attempt_id="attempt-1",
        payload=bound_payload,
        resource_refs=refs,
    )


def test_exact_malformed_legacy_and_authorized_canonical_pair_is_narrowly_bound():
    records = (
        _record(4, "interaction_resolved"),
        _record(
            5,
            "interaction.resolved",
            payload=_payload(),
            resource_ref_id="answer-artifact",
        ),
    )

    [pair] = legacy_interaction_resolution_supersession_pairs(records)

    assert pair.legacy_ordinal == 4
    assert pair.canonical_ordinal == 5
    assert pair.scope == (
        "execution-1",
        "generation-1",
        "attempt-1",
        "interaction-1",
    )
    assert legacy_interaction_resolution_supersessions(records) == frozenset({4})


@pytest.mark.parametrize(
    "records",
    (
        (
            _record(4, "interaction_resolved"),
            _record(
                5,
                "interaction.resolved",
                payload=_payload(),
                resource_ref_id="foreign-artifact",
            ),
        ),
        (
            _record(4, "interaction_resolved"),
            _record(
                5,
                "interaction.resolved",
                payload=_payload(),
                resource_ref_id="answer-artifact",
            ),
            _record(
                6,
                "interaction.resolved",
                payload=_payload(ref_id="answer-artifact-2"),
                resource_ref_id="answer-artifact-2",
            ),
        ),
        (
            _record(
                4,
                "interaction_resolved",
                payload=_payload(),
                resource_ref_id="answer-artifact",
            ),
            _record(
                5,
                "interaction.resolved",
                payload=_payload(),
                resource_ref_id="answer-artifact",
            ),
        ),
    ),
)
def test_foreign_ref_multiple_or_complete_legacy_evidence_fails_closed(records):
    with pytest.raises(
        InteractionResolutionCompatibilityError,
        match="ambiguous",
    ):
        legacy_interaction_resolution_supersessions(records)


def test_cross_interaction_evidence_never_supersedes():
    records = (
        _record(4, "interaction_resolved", interaction_id="interaction-old"),
        _record(
            5,
            "interaction.resolved",
            interaction_id="interaction-new",
            payload=_payload(interaction_id="interaction-new"),
            resource_ref_id="answer-artifact",
        ),
    )

    assert legacy_interaction_resolution_supersessions(records) == frozenset()


def test_canonical_event_cannot_retroactively_authorize_later_legacy_evidence():
    records = (
        _record(
            4,
            "interaction.resolved",
            payload=_payload(),
            resource_ref_id="answer-artifact",
        ),
        _record(5, "interaction_resolved"),
    )

    with pytest.raises(
        InteractionResolutionCompatibilityError,
        match="ambiguous",
    ):
        legacy_interaction_resolution_supersessions(records)
