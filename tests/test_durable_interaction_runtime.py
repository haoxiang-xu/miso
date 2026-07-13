from __future__ import annotations

import copy

import pytest

from unchain.interaction.durable import (
    INTERACTION_JOURNAL_KEY,
    INTERACTION_KIND_HUMAN_INPUT,
    INTERACTION_KIND_MAX_BUDGET,
    INTERACTION_KIND_TOOL_APPROVAL,
    InteractionReceiptConflictError,
    build_interaction_request,
    mark_interaction_applied,
    new_interaction_journal,
    register_interaction_request,
)
from unchain.interaction.runtime import (
    DurableInteractionRuntime,
    response_contract_for_kind,
)
from unchain.memory import InMemorySessionStore, KernelMemoryRuntime


def _install_pending(
    *,
    kind: str,
    payload: dict,
    occurrence: str = "occurrence-1",
):
    session_id = f"session-{kind}"
    memory = KernelMemoryRuntime.from_config(store=InMemorySessionStore())
    initial = memory.load_session_snapshot(session_id)
    request = build_interaction_request(
        session_id=session_id,
        kind=kind,
        source_run_id="run-1",
        occurrence=occurrence,
        payload=payload,
        response_contract=response_contract_for_kind(kind),
        created_revision=int(initial.revision or 0),
        subject={"provider": "openai", "model": "gpt-5"},
    )
    journal = register_interaction_request(
        new_interaction_journal(),
        request,
        checkpoint_id="checkpoint-1",
    )
    persisted = memory.save_session_state(
        session_id,
        {INTERACTION_JOURNAL_KEY: journal},
        expected_revision=initial.revision,
    )
    return memory, DurableInteractionRuntime(memory, clock_ms=lambda: 123), request, persisted


def test_human_receipt_is_normalized_persisted_and_idempotent() -> None:
    memory, runtime, request, pending = _install_pending(
        kind=INTERACTION_KIND_HUMAN_INPUT,
        payload={
            "request_id": "call-user",
            "kind": "selector",
            "title": "Choose",
            "question": "Which one?",
            "selection_mode": "single",
            "options": [
                {"label": "A", "value": "a", "description": ""},
                {"label": "B", "value": "b", "description": ""},
            ],
            "allow_other": False,
            "other_label": "Other",
            "other_placeholder": "",
            "min_selected": 1,
            "max_selected": 1,
        },
    )
    response = {
        "request_id": "call-user",
        "selected_values": ["a"],
        "other_text": None,
    }

    first = runtime.record_receipt(
        request.session_id,
        interaction_id=request.interaction_id,
        response=response,
        submitted_by="ui:test",
        expected_revision=pending.revision,
    )
    assert first.receipt is not None
    assert first.receipt.submitted_by == "ui:test"
    assert first.response == response
    first_revision = first.session_snapshot.revision

    retry = runtime.record_receipt(
        request.session_id,
        interaction_id=request.interaction_id,
        response=response,
        submitted_by="ui:test",
        expected_revision=pending.revision,
    )
    assert retry.receipt == first.receipt
    assert retry.session_snapshot.revision == first_revision
    assert memory.load_session_snapshot(request.session_id).revision == first_revision


def test_conflicting_human_receipt_fails_closed() -> None:
    _, runtime, request, pending = _install_pending(
        kind=INTERACTION_KIND_HUMAN_INPUT,
        payload={
            "request_id": "call-user",
            "kind": "selector",
            "title": "Choose",
            "question": "Which one?",
            "selection_mode": "single",
            "options": [
                {"label": "A", "value": "a", "description": ""},
                {"label": "B", "value": "b", "description": ""},
            ],
            "allow_other": False,
            "other_label": "Other",
            "other_placeholder": "",
            "min_selected": 1,
            "max_selected": 1,
        },
    )
    runtime.record_receipt(
        request.session_id,
        interaction_id=request.interaction_id,
        response={
            "request_id": "call-user",
            "selected_values": ["a"],
            "other_text": None,
        },
        expected_revision=pending.revision,
    )
    with pytest.raises(InteractionReceiptConflictError):
        runtime.record_receipt(
            request.session_id,
            interaction_id=request.interaction_id,
            response={
                "request_id": "call-user",
                "selected_values": ["b"],
                "other_text": None,
            },
        )


def test_tool_and_budget_receipts_have_one_canonical_shape() -> None:
    _, tool_runtime, tool_request, tool_pending = _install_pending(
        kind=INTERACTION_KIND_TOOL_APPROVAL,
        payload={"tool_name": "write_file", "call_id": "call-1"},
    )
    tool = tool_runtime.record_receipt(
        tool_request.session_id,
        interaction_id=tool_request.interaction_id,
        response=True,
        expected_revision=tool_pending.revision,
    )
    assert tool.response == {
        "approved": True,
        "modified_arguments": None,
        "reason": "",
    }

    _, budget_runtime, budget_request, budget_pending = _install_pending(
        kind=INTERACTION_KIND_MAX_BUDGET,
        payload={"suggested_extra_iterations": 4},
    )
    budget = budget_runtime.record_receipt(
        budget_request.session_id,
        interaction_id=budget_request.interaction_id,
        response={"approved": True},
        expected_revision=budget_pending.revision,
    )
    assert budget.response == {"approved": True, "extra_iterations": 4}


def test_invalid_response_is_rejected_before_any_write() -> None:
    memory, runtime, request, pending = _install_pending(
        kind=INTERACTION_KIND_MAX_BUDGET,
        payload={"suggested_extra_iterations": 2},
    )
    with pytest.raises(ValueError, match="positive integer"):
        runtime.record_receipt(
            request.session_id,
            interaction_id=request.interaction_id,
            response={"approved": True, "extra_iterations": 0},
            expected_revision=pending.revision,
        )
    assert memory.load_session_snapshot(request.session_id).revision == pending.revision


def test_same_receipt_retry_succeeds_after_application() -> None:
    memory, runtime, request, pending = _install_pending(
        kind=INTERACTION_KIND_TOOL_APPROVAL,
        payload={"tool_name": "write_file", "call_id": "call-1"},
    )
    first = runtime.record_receipt(
        request.session_id,
        interaction_id=request.interaction_id,
        response=True,
        expected_revision=pending.revision,
    )
    assert first.receipt is not None

    state = copy.deepcopy(first.session_snapshot.state)
    state[INTERACTION_JOURNAL_KEY] = mark_interaction_applied(
        state[INTERACTION_JOURNAL_KEY],
        interaction_id=request.interaction_id,
        receipt_id=first.receipt.receipt_id,
        applied_checkpoint_id="checkpoint-2",
    )
    applied = memory.save_session_state(
        request.session_id,
        state,
        expected_revision=first.session_snapshot.revision,
    )

    retry = runtime.record_receipt(
        request.session_id,
        interaction_id=request.interaction_id,
        response=True,
        expected_revision=pending.revision,
    )

    assert retry.receipt == first.receipt
    assert retry.application is not None
    assert retry.session_snapshot.revision == applied.revision


def test_receipt_write_reports_success_if_another_worker_applies_before_verify() -> None:
    memory, runtime, request, pending = _install_pending(
        kind=INTERACTION_KIND_TOOL_APPROVAL,
        payload={"tool_name": "write_file", "call_id": "call-1"},
    )
    original_save = memory.save_session_state
    applied_during_verify = False

    def save_then_apply(
        session_id,
        state,
        *,
        expected_revision=None,
        execution_fence=None,
    ):
        nonlocal applied_during_verify
        saved = original_save(
            session_id,
            state,
            expected_revision=expected_revision,
            execution_fence=execution_fence,
        )
        journal = saved.state.get(INTERACTION_JOURNAL_KEY)
        entry = (
            journal.get("entries", {}).get(request.interaction_id)
            if isinstance(journal, dict)
            else None
        )
        if (
            not applied_during_verify
            and isinstance(entry, dict)
            and isinstance(entry.get("receipt"), dict)
        ):
            applied_during_verify = True
            applied_state = copy.deepcopy(saved.state)
            applied_state[INTERACTION_JOURNAL_KEY] = mark_interaction_applied(
                journal,
                interaction_id=request.interaction_id,
                receipt_id=entry["receipt"]["receipt_id"],
                applied_checkpoint_id="checkpoint-2",
            )
            original_save(
                session_id,
                applied_state,
                expected_revision=saved.revision,
            )
        return saved

    memory.save_session_state = save_then_apply

    result = runtime.record_receipt(
        request.session_id,
        interaction_id=request.interaction_id,
        response=True,
        expected_revision=pending.revision,
    )

    assert applied_during_verify is True
    assert result.receipt is not None
    assert result.application is not None
    assert result.response == {
        "approved": True,
        "modified_arguments": None,
        "reason": "",
    }


def test_concurrent_identical_receipt_submission_converges_on_the_winner() -> None:
    memory, runtime, request, pending = _install_pending(
        kind=INTERACTION_KIND_TOOL_APPROVAL,
        payload={"tool_name": "write_file", "call_id": "call-1"},
    )
    original_save = memory.save_session_state
    injected_winner = False

    def save_after_identical_winner(
        session_id,
        state,
        *,
        expected_revision=None,
        execution_fence=None,
    ):
        nonlocal injected_winner
        if not injected_winner:
            injected_winner = True
            original_save(
                session_id,
                state,
                expected_revision=expected_revision,
                execution_fence=execution_fence,
            )
        return original_save(
            session_id,
            state,
            expected_revision=expected_revision,
            execution_fence=execution_fence,
        )

    memory.save_session_state = save_after_identical_winner

    result = runtime.record_receipt(
        request.session_id,
        interaction_id=request.interaction_id,
        response=True,
        expected_revision=pending.revision,
    )

    assert injected_winner is True
    assert result.receipt is not None
    assert result.response == {
        "approved": True,
        "modified_arguments": None,
        "reason": "",
    }
    assert memory.load_session_snapshot(request.session_id).revision == (
        pending.revision + 1
    )
