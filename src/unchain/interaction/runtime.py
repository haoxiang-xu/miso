"""Persistence facade for durable interaction requests and receipts.

The request itself is written atomically with an execution checkpoint by the
memory checkpoint harness.  This facade owns the second transition: recording
one normalized answer in the same revisioned session document before any
resume delta is allowed to run.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any, Callable

from ..execution import ExecutionCancelledError, ExecutionFence
from ..input.human_input import HumanInputRequest, HumanInputResponse
from ..memory.revision import SessionRevisionConflictError, SessionSnapshot
from ..memory.runtime import KernelMemoryRuntime
from ..tools.models import ToolConfirmationResponse
from .durable import (
    INTERACTION_JOURNAL_KEY,
    INTERACTION_KIND_HUMAN_INPUT,
    INTERACTION_KIND_MAX_BUDGET,
    INTERACTION_KIND_TOOL_APPROVAL,
    InteractionIntegrityError,
    InteractionNotPendingError,
    InteractionReceipt,
    InteractionRequest,
    build_interaction_receipt,
    record_interaction_receipt,
    strict_json_digest,
    validate_interaction_journal,
)
from ..memory.checkpoint_state import (
    EXECUTION_CHECKPOINT_DOMAIN_KEY,
    EXECUTION_CHECKPOINT_KEY,
    apply_checkpoint_interaction_receipt,
    validate_execution_checkpoint,
)


HUMAN_INPUT_RESPONSE_CONTRACT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "request_id": {"type": "string"},
        "selected_values": {
            "type": "array",
            "items": {"type": "string"},
        },
        "other_text": {"type": ["string", "null"]},
    },
    "required": ["request_id", "selected_values", "other_text"],
    "additionalProperties": False,
}

TOOL_APPROVAL_RESPONSE_CONTRACT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "modified_arguments": {"type": ["object", "null"]},
        "reason": {"type": "string"},
    },
    "required": ["approved", "modified_arguments", "reason"],
    "additionalProperties": False,
}

MAX_BUDGET_RESPONSE_CONTRACT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approved": {"type": "boolean"},
        "extra_iterations": {"type": "integer", "minimum": 0},
    },
    "required": ["approved", "extra_iterations"],
    "additionalProperties": False,
}


def response_contract_for_kind(kind: str) -> dict[str, Any]:
    if kind == INTERACTION_KIND_HUMAN_INPUT:
        return copy.deepcopy(HUMAN_INPUT_RESPONSE_CONTRACT)
    if kind == INTERACTION_KIND_TOOL_APPROVAL:
        return copy.deepcopy(TOOL_APPROVAL_RESPONSE_CONTRACT)
    if kind == INTERACTION_KIND_MAX_BUDGET:
        return copy.deepcopy(MAX_BUDGET_RESPONSE_CONTRACT)
    raise InteractionIntegrityError(f"unsupported interaction kind: {kind!r}")


def _normalize_human_input_response(
    request: InteractionRequest,
    response: Any,
) -> dict[str, Any]:
    human_request = HumanInputRequest.from_dict(request.payload)
    return HumanInputResponse.from_raw(response, request=human_request).to_dict()


def _normalize_tool_approval_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, (bool, dict, ToolConfirmationResponse)):
        raise ValueError("tool approval response must be a bool or object")
    parsed = ToolConfirmationResponse.from_raw(response)
    if not isinstance(parsed.approved, bool):
        raise ValueError("tool approval response approved must be a boolean")
    modified_arguments = parsed.modified_arguments
    if modified_arguments is not None and not isinstance(modified_arguments, dict):
        raise ValueError(
            "tool approval response modified_arguments must be an object or null"
        )
    if not isinstance(parsed.reason, str):
        raise ValueError("tool approval response reason must be a string")
    return {
        "approved": parsed.approved,
        "modified_arguments": copy.deepcopy(modified_arguments),
        "reason": parsed.reason,
    }


def _normalize_max_budget_response(
    request: InteractionRequest,
    response: Any,
) -> dict[str, Any]:
    if isinstance(response, bool):
        approved = response
        raw_extra = None
    elif isinstance(response, dict):
        approved = response.get("approved", False)
        raw_extra = response.get("extra_iterations")
    else:
        raise ValueError("max budget response must be a bool or object")
    if not isinstance(approved, bool):
        raise ValueError("max budget response approved must be a boolean")
    if not approved:
        return {"approved": False, "extra_iterations": 0}

    payload = request.payload if isinstance(request.payload, dict) else {}
    default_extra = payload.get("suggested_extra_iterations", 1)
    extra = default_extra if raw_extra is None else raw_extra
    if isinstance(extra, bool) or not isinstance(extra, int) or extra < 1:
        raise ValueError(
            "approved max budget response extra_iterations must be a positive integer"
        )
    return {"approved": True, "extra_iterations": extra}


def normalize_interaction_response(
    request: InteractionRequest | dict[str, Any],
    response: Any,
) -> dict[str, Any]:
    bound_request = InteractionRequest.from_dict(request)
    current_contract = response_contract_for_kind(bound_request.kind)
    if bound_request.schema_digest != strict_json_digest(current_contract):
        raise InteractionIntegrityError(
            "pending interaction response schema no longer matches this runtime"
        )
    if bound_request.kind == INTERACTION_KIND_HUMAN_INPUT:
        return _normalize_human_input_response(bound_request, response)
    if bound_request.kind == INTERACTION_KIND_TOOL_APPROVAL:
        return _normalize_tool_approval_response(response)
    if bound_request.kind == INTERACTION_KIND_MAX_BUDGET:
        return _normalize_max_budget_response(bound_request, response)
    raise InteractionIntegrityError(
        f"unsupported interaction kind: {bound_request.kind!r}"
    )


@dataclass(frozen=True, slots=True)
class DurableInteractionSnapshot:
    request: InteractionRequest
    checkpoint_id: str
    receipt: InteractionReceipt | None
    application: dict[str, Any] | None
    session_snapshot: SessionSnapshot

    @property
    def response(self) -> dict[str, Any] | None:
        if self.receipt is None:
            return None
        return copy.deepcopy(self.receipt.response)


def require_unapplied_callback_receipt(
    snapshot: DurableInteractionSnapshot,
) -> DurableInteractionSnapshot:
    if snapshot.receipt is None:
        raise InteractionNotPendingError(
            "callback response did not produce a durable receipt"
        )
    if snapshot.application is not None:
        raise InteractionNotPendingError(
            "interaction receipt was already applied by another worker"
        )
    return snapshot


def _verify_fence(
    memory_runtime: KernelMemoryRuntime,
    execution_fence: ExecutionFence | None,
) -> None:
    if execution_fence is None:
        return
    verify_lease = getattr(memory_runtime.store, "verify_lease", None)
    if not callable(verify_lease):
        raise TypeError("execution fence requires a lease-aware session store")
    verify_lease(
        execution_fence.execution_id,
        execution_fence.owner_id,
        execution_fence.fencing_token,
    )


@dataclass
class DurableInteractionRuntime:
    memory_runtime: KernelMemoryRuntime
    clock_ms: Callable[[], int] = lambda: int(time.time() * 1000)

    def _load(
        self,
        session_id: str,
        *,
        interaction_id: str | None = None,
        require_active: bool = False,
        reconcile_cancelled: bool,
    ) -> DurableInteractionSnapshot:
        if not isinstance(session_id, str) or not session_id.strip():
            raise InteractionNotPendingError(
                "durable interaction requires a non-empty session_id"
            )
        if reconcile_cancelled:
            session_snapshot, cancellation = (
                self.memory_runtime.reconcile_cancelled_execution_checkpoint(
                    session_id
                )
            )
            if cancellation is not None:
                raise ExecutionCancelledError(
                    cancellation.reason or "execution was cancelled",
                    execution_id=cancellation.execution_id,
                    owner_id=cancellation.owner_id,
                    fencing_token=cancellation.fencing_token,
                )
        else:
            session_snapshot = self.memory_runtime.load_session_snapshot(
                session_id
            )
        journal = validate_interaction_journal(
            session_snapshot.state.get(INTERACTION_JOURNAL_KEY)
        )
        resolved_id = interaction_id or journal.get("active_id")
        if not isinstance(resolved_id, str) or not resolved_id:
            raise InteractionNotPendingError(
                f"session has no pending interaction: {session_id!r}"
            )
        if require_active and journal.get("active_id") != resolved_id:
            raise InteractionNotPendingError(
                f"interaction is no longer pending: {resolved_id!r}"
            )
        entry = journal["entries"].get(resolved_id)
        if not isinstance(entry, dict):
            raise InteractionNotPendingError(
                f"interaction does not exist: {resolved_id!r}"
            )
        request = InteractionRequest.from_dict(entry.get("request"))
        receipt_raw = entry.get("receipt")
        receipt = (
            InteractionReceipt.from_dict(receipt_raw, request=request)
            if receipt_raw is not None
            else None
        )
        return DurableInteractionSnapshot(
            request=request,
            checkpoint_id=str(entry.get("checkpoint_id") or ""),
            receipt=receipt,
            application=copy.deepcopy(entry.get("application")),
            session_snapshot=session_snapshot,
        )

    def load(
        self,
        session_id: str,
        *,
        interaction_id: str | None = None,
        require_active: bool = False,
    ) -> DurableInteractionSnapshot:
        return self._load(
            session_id,
            interaction_id=interaction_id,
            require_active=require_active,
            reconcile_cancelled=True,
        )

    def load_active(self, session_id: str) -> DurableInteractionSnapshot:
        return self.load(session_id, require_active=True)

    def record_receipt(
        self,
        session_id: str,
        *,
        interaction_id: str,
        response: Any,
        submitted_by: str = "user",
        expected_revision: int | None = None,
        execution_fence: ExecutionFence | None = None,
    ) -> DurableInteractionSnapshot:
        current = self.load(
            session_id,
            interaction_id=interaction_id,
            require_active=False,
        )
        effective_fence = self.memory_runtime.resolve_interaction_execution_fence(
            session_id,
            checkpoint_id=current.checkpoint_id,
            session_snapshot=current.session_snapshot,
            execution_fence=execution_fence,
        )
        normalized_response = normalize_interaction_response(
            current.request,
            response,
        )
        if (
            current.receipt is not None
            and current.receipt.response == normalized_response
        ):
            try:
                _verify_fence(self.memory_runtime, effective_fence)
            except ExecutionCancelledError:
                self.memory_runtime.reconcile_cancelled_execution_checkpoint(
                    session_id
                )
                raise
            return current
        receipt = build_interaction_receipt(
            current.request,
            normalized_response,
            submitted_by=submitted_by,
            submitted_at_ms=int(self.clock_ms()),
        )
        state = copy.deepcopy(current.session_snapshot.state)
        journal = validate_interaction_journal(state.get(INTERACTION_JOURNAL_KEY))
        updated_journal = record_interaction_receipt(journal, receipt)
        if updated_journal == journal:
            try:
                _verify_fence(self.memory_runtime, effective_fence)
            except ExecutionCancelledError:
                self.memory_runtime.reconcile_cancelled_execution_checkpoint(
                    session_id
                )
                raise
            return DurableInteractionSnapshot(
                request=current.request,
                checkpoint_id=current.checkpoint_id,
                receipt=InteractionReceipt.from_dict(
                    journal["entries"][interaction_id]["receipt"],
                    request=current.request,
                ),
                application=copy.deepcopy(
                    journal["entries"][interaction_id]["application"]
                ),
                session_snapshot=current.session_snapshot,
            )

        if (
            expected_revision is not None
            and current.session_snapshot.revision is not None
            and current.session_snapshot.revision != expected_revision
        ):
            raise SessionRevisionConflictError(
                session_id=session_id,
                expected_revision=expected_revision,
                actual_revision=current.session_snapshot.revision,
            )
        state[INTERACTION_JOURNAL_KEY] = updated_journal
        try:
            persisted = self.memory_runtime.save_interaction_session_state(
                session_id,
                state,
                checkpoint_id=current.checkpoint_id,
                session_snapshot=current.session_snapshot,
                expected_revision=(
                    expected_revision
                    if expected_revision is not None
                    else current.session_snapshot.revision
                ),
                execution_fence=execution_fence,
            )
        except ExecutionCancelledError:
            self.memory_runtime.reconcile_cancelled_execution_checkpoint(
                session_id
            )
            raise
        except SessionRevisionConflictError:
            winner = self.load(
                session_id,
                interaction_id=interaction_id,
                require_active=False,
            )
            if (
                winner.receipt is not None
                and winner.receipt.response == normalized_response
            ):
                try:
                    _verify_fence(self.memory_runtime, effective_fence)
                except ExecutionCancelledError:
                    self.memory_runtime.reconcile_cancelled_execution_checkpoint(
                        session_id
                    )
                    raise
                return winner
            raise
        verified = self.load(
            session_id,
            interaction_id=interaction_id,
            require_active=False,
        )
        if verified.receipt is None or verified.receipt.receipt_id != receipt.receipt_id:
            if (
                persisted.revision is not None
                and verified.session_snapshot.revision is not None
                and persisted.revision != verified.session_snapshot.revision
            ):
                raise SessionRevisionConflictError(
                    session_id=session_id,
                    expected_revision=persisted.revision,
                    actual_revision=verified.session_snapshot.revision,
                )
            raise InteractionIntegrityError(
                "interaction receipt write verification failed"
            )
        return verified

    def cancel_pending(
        self,
        session_id: str,
        *,
        source_run_id: str,
        expected_interaction_id: str | None = None,
        reason: str = "execution_cancelled",
        submitted_by: str = "runtime:cancel_execution",
    ) -> DurableInteractionSnapshot | None:
        """Atomically abandon one source run's checkpoint and active interaction.

        Legacy source-only callers must revoke the execution lease first.  A
        host that supplies ``expected_interaction_id`` may use this CAS as the
        durable cancellation claim before revoking the lease; a mismatch then
        leaves both the checkpoint and newer interaction untouched.  The exact
        source and interaction bindings prevent a late stop for an older wait
        from consuming a newer interaction.
        """

        normalized_source_run_id = str(source_run_id or "").strip()
        if not normalized_source_run_id:
            raise ValueError("source_run_id must be a non-empty string")
        normalized_interaction_id: str | None = None
        if expected_interaction_id is not None:
            if not isinstance(expected_interaction_id, str):
                raise ValueError("expected_interaction_id must be a string")
            normalized_interaction_id = expected_interaction_id.strip()
            if not normalized_interaction_id:
                raise ValueError(
                    "expected_interaction_id must be a non-empty string"
                )
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        current = self._load(
            session_id,
            require_active=True,
            reconcile_cancelled=False,
        )
        if current.request.source_run_id != normalized_source_run_id:
            raise InteractionNotPendingError(
                "active interaction belongs to a different source run"
            )
        if (
            normalized_interaction_id is not None
            and current.request.interaction_id != normalized_interaction_id
        ):
            raise InteractionNotPendingError(
                "active interaction is a different interaction"
            )

        state = copy.deepcopy(current.session_snapshot.state)
        checkpoint_raw = state.get(EXECUTION_CHECKPOINT_KEY)
        if not isinstance(checkpoint_raw, dict):
            raise InteractionIntegrityError(
                "active interaction has no execution checkpoint"
            )
        checkpoint = validate_execution_checkpoint(checkpoint_raw)
        if (
            checkpoint.get("source_run_id") != normalized_source_run_id
            or checkpoint.get("checkpoint_id") != current.checkpoint_id
        ):
            raise InteractionIntegrityError(
                "active interaction checkpoint belongs to a different source run"
            )

        journal = validate_interaction_journal(
            state.get(INTERACTION_JOURNAL_KEY)
        )
        entry = journal["entries"].get(current.request.interaction_id)
        if not isinstance(entry, dict):
            raise InteractionIntegrityError(
                "active interaction is missing from its journal"
            )
        receipt_raw = entry.get("receipt")
        if receipt_raw is None:
            cancellation_receipt = build_interaction_receipt(
                current.request,
                {
                    "cancelled": True,
                    "reason": reason or "execution_cancelled",
                },
                submitted_by=submitted_by,
                submitted_at_ms=int(self.clock_ms()),
            )
            journal = record_interaction_receipt(
                journal,
                cancellation_receipt,
            )
            state[INTERACTION_JOURNAL_KEY] = journal

        apply_checkpoint_interaction_receipt(
            state,
            checkpoint=checkpoint,
            applied_checkpoint_id=f"cancelled:{current.checkpoint_id}",
        )
        state.pop(EXECUTION_CHECKPOINT_KEY, None)
        state.pop(EXECUTION_CHECKPOINT_DOMAIN_KEY, None)
        try:
            self.memory_runtime.save_interaction_session_state(
                session_id,
                state,
                checkpoint_id=current.checkpoint_id,
                session_snapshot=current.session_snapshot,
                expected_revision=current.session_snapshot.revision,
                execution_fence=None,
            )
        except SessionRevisionConflictError:
            winner = self._load(
                session_id,
                interaction_id=current.request.interaction_id,
                require_active=False,
                reconcile_cancelled=False,
            )
            if winner.application is not None:
                if winner.application.get("applied_checkpoint_id") == (
                    f"cancelled:{current.checkpoint_id}"
                ):
                    return winner
                raise InteractionNotPendingError(
                    "interaction was already applied without cancellation"
                )
            raise

        cancelled = self._load(
            session_id,
            interaction_id=current.request.interaction_id,
            require_active=False,
            reconcile_cancelled=False,
        )
        if cancelled.application is None:
            raise InteractionIntegrityError(
                "cancelled interaction was not terminalized"
            )
        if cancelled.application.get("applied_checkpoint_id") != (
            f"cancelled:{current.checkpoint_id}"
        ):
            raise InteractionIntegrityError(
                "cancelled interaction has a foreign application"
            )
        return cancelled

    def require_receipt(
        self,
        session_id: str,
        *,
        interaction_id: str | None = None,
    ) -> DurableInteractionSnapshot:
        current = self.load(
            session_id,
            interaction_id=interaction_id,
            require_active=True,
        )
        if current.receipt is None:
            raise InteractionNotPendingError(
                f"interaction has no recorded receipt: {current.request.interaction_id!r}"
            )
        normalized = normalize_interaction_response(
            current.request,
            current.receipt.response,
        )
        if normalized != current.receipt.response:
            raise InteractionIntegrityError(
                "recorded interaction receipt is not canonical for this runtime"
            )
        return current


__all__ = [
    "DurableInteractionRuntime",
    "DurableInteractionSnapshot",
    "HUMAN_INPUT_RESPONSE_CONTRACT",
    "MAX_BUDGET_RESPONSE_CONTRACT",
    "TOOL_APPROVAL_RESPONSE_CONTRACT",
    "normalize_interaction_response",
    "require_unapplied_callback_receipt",
    "response_contract_for_kind",
]
