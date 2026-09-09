from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from unchain.context.models import (
    MAX_PINNED_TASK_STATE_BYTES,
    MAX_PINNED_TASK_STATE_ITEMS,
    PinnedTaskState,
)
from unchain.journal import ModelValidationError, OperationRef, ResourceRef
from unchain.journal.models import _required_text

from .operations import build_operation_ref
from .ports import (
    BoundPinnedTaskStateRepository,
    BoundWorkspaceReferenceAuthorizer,
    RepositoryConflictError,
    RepositoryScopeError,
    WorkspaceRepositoryError,
)


_PERSISTENCE_FIELDS = (
    "objective",
    "success_criteria",
    "constraints",
    "confirmed_decisions",
    "open_questions",
    "active_plan",
    "artifact_refs",
    "memory_refs",
    "status",
)
_PATCH_FIELDS = frozenset(_PERSISTENCE_FIELDS)
_LIST_FIELDS = frozenset(
    {
        "success_criteria",
        "constraints",
        "confirmed_decisions",
        "open_questions",
        "active_plan",
    }
)
MAX_TASK_STATE_ITEMS = MAX_PINNED_TASK_STATE_ITEMS
MAX_TASK_STATE_BYTES = MAX_PINNED_TASK_STATE_BYTES


def _identity_patch_redactor(patch: Mapping[str, Any]) -> Mapping[str, Any]:
    return patch


def _refs(value: object, field_name: str) -> tuple[ResourceRef, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError(f"{field_name} must be an array")
    return tuple(
        item if isinstance(item, ResourceRef) else ResourceRef.from_dict(item)
        for item in value
    )


def _merge_refs(
    first: Sequence[ResourceRef],
    second: Sequence[ResourceRef],
) -> tuple[ResourceRef, ...]:
    merged: list[ResourceRef] = []
    for ref in (*first, *second):
        if ref not in merged:
            merged.append(ref)
    return tuple(merged)


class TaskStateService:
    """CAS-only updates for the task state that the compiler always pins."""

    def __init__(
        self,
        *,
        repository: BoundPinnedTaskStateRepository,
        references: BoundWorkspaceReferenceAuthorizer,
        patch_redactor: Callable[[Mapping[str, Any]], Mapping[str, Any]]
        | None = None,
    ) -> None:
        if not isinstance(repository, BoundPinnedTaskStateRepository):
            raise TypeError("repository must be a BoundPinnedTaskStateRepository")
        if not isinstance(references, BoundWorkspaceReferenceAuthorizer):
            raise TypeError("references must be a BoundWorkspaceReferenceAuthorizer")
        if repository.binding_id != references.binding_id:
            raise RepositoryScopeError("task state capabilities have different bindings")
        if patch_redactor is not None and not callable(patch_redactor):
            raise TypeError("patch_redactor must be callable")
        self._repository = repository
        self._references = references
        self._patch_redactor = patch_redactor or _identity_patch_redactor

    @property
    def binding_id(self) -> str:
        return self._references.binding_id

    def get(self) -> PinnedTaskState | None:
        state = self._repository.current()
        if state is not None and not isinstance(state, PinnedTaskState):
            raise WorkspaceRepositoryError("task state capability returned an invalid record")
        if state is not None:
            self._validate_identity(state)
            self._validate_state_bounds(state)
        return state

    def update(
        self,
        *,
        expected_revision: int | None,
        patch: Mapping[str, Any],
        source_event_refs: Sequence[ResourceRef],
        operation_id: str,
    ) -> PinnedTaskState:
        if not isinstance(patch, Mapping) or not patch:
            raise ModelValidationError("task state patch must not be empty")
        unknown = set(patch) - _PATCH_FIELDS
        if unknown:
            raise ModelValidationError(
                f"unknown task state fields: {', '.join(sorted(unknown))}"
            )
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 1
        ):
            raise ValueError("expected_revision must be a positive integer")
        source_refs = self._authorize_refs(
            source_event_refs,
            field_name="source_event_refs",
            allowed_kinds=frozenset({"context_event"}),
        )
        if not source_refs:
            raise ModelValidationError("task state updates require source event provenance")
        normalized_patch = self._normalize_patch(patch)
        normalized_patch = self._redact_normalized_patch(normalized_patch)
        operation = build_operation_ref(
            operation_id,
            domain="workspace.task_state.update",
            payload={
                "expected_revision": expected_revision,
                "patch": normalized_patch,
                "source_event_refs": source_refs,
            },
        )
        replayed = self._repository.replay(operation=operation)
        if replayed is not None:
            return self._validate_replay(
                replayed,
                expected_revision=expected_revision,
                patch=normalized_patch,
                source_refs=source_refs,
                operation=operation,
            )

        current = self.get()
        actual_revision = current.revision if current is not None else None
        if actual_revision != expected_revision:
            raise RepositoryConflictError("task state revision changed")
        base: dict[str, Any]
        if current is None:
            base = {
                "state_id": self._repository.state_id,
                "revision": 1,
                "objective": "",
                "success_criteria": (),
                "constraints": (),
                "confirmed_decisions": (),
                "open_questions": (),
                "active_plan": (),
                "artifact_refs": (),
                "memory_refs": (),
                "source_event_refs": (),
                "status": "in_progress",
            }
        else:
            base = {
                field_name: getattr(current, field_name)
                for field_name in (
                    "state_id",
                    "revision",
                    "objective",
                    "success_criteria",
                    "constraints",
                    "confirmed_decisions",
                    "open_questions",
                    "active_plan",
                    "artifact_refs",
                    "memory_refs",
                    "source_event_refs",
                    "status",
                )
            }
            base["revision"] = current.revision + 1

        base.update(normalized_patch)
        if not base["objective"]:
            raise ModelValidationError("initial task state requires an objective")
        persistence_projection = {
            field_name: base[field_name]
            for field_name in _PERSISTENCE_FIELDS
        }
        base.update(self._redact_normalized_patch(persistence_projection))
        base["source_event_refs"] = source_refs
        state = PinnedTaskState(**base)
        self._validate_state_bounds(state)
        persisted = self._repository.compare_and_swap(
            state=state,
            expected_revision=expected_revision,
            operation=operation,
        )
        if persisted != state:
            raise WorkspaceRepositoryError("task state capability returned a divergent revision")
        return persisted

    def _normalize_patch(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for field_name, value in patch.items():
            if field_name in _LIST_FIELDS:
                if not isinstance(value, Sequence) or isinstance(
                    value, (str, bytes, bytearray)
                ):
                    raise TypeError(f"{field_name} must be an array")
                normalized[field_name] = tuple(
                    _required_text(
                        item,
                        f"{field_name}[{index}]",
                        maximum=16_384,
                    )
                    for index, item in enumerate(value)
                )
            elif field_name in {"artifact_refs", "memory_refs"}:
                allowed = (
                    frozenset({"artifact"})
                    if field_name == "artifact_refs"
                    else frozenset({"memory"})
                )
                normalized[field_name] = self._authorize_refs(
                    _refs(value, field_name),
                    field_name=field_name,
                    allowed_kinds=allowed,
                )
            elif field_name == "objective":
                normalized[field_name] = _required_text(
                    value,
                    field_name,
                    maximum=32_768,
                )
            elif field_name == "status":
                normalized[field_name] = _required_text(
                    value,
                    field_name,
                    identifier=True,
                )
        return normalized

    def _redact_normalized_patch(
        self,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        protected_fields = ("artifact_refs", "memory_refs", "status")

        def apply_once(value: Mapping[str, Any]) -> dict[str, Any]:
            try:
                redacted_raw = self._patch_redactor(dict(value))
            except Exception:
                raise WorkspaceRepositoryError(
                    "task state patch redaction failed"
                ) from None
            if not isinstance(redacted_raw, Mapping) or set(redacted_raw) != set(value):
                raise WorkspaceRepositoryError("task state patch redaction failed")
            try:
                redacted = self._normalize_patch(redacted_raw)
            except Exception:
                raise WorkspaceRepositoryError(
                    "task state patch redaction failed"
                ) from None
            if any(
                redacted.get(field_name) != value.get(field_name)
                for field_name in protected_fields
                if field_name in value
            ):
                raise WorkspaceRepositoryError("task state patch redaction failed")
            return redacted

        redacted = apply_once(patch)
        if apply_once(redacted) != redacted:
            raise WorkspaceRepositoryError("task state patch redaction failed")
        return redacted

    def _validate_replay(
        self,
        state: PinnedTaskState,
        *,
        expected_revision: int | None,
        patch: Mapping[str, Any],
        source_refs: tuple[ResourceRef, ...],
        operation: OperationRef,
    ) -> PinnedTaskState:
        if not isinstance(state, PinnedTaskState):
            raise WorkspaceRepositoryError("task state replay returned an invalid record")
        self._validate_identity(state)
        self._validate_state_bounds(state)
        expected_next = 1 if expected_revision is None else expected_revision + 1
        if state.revision != expected_next:
            raise WorkspaceRepositoryError("task state replay returned a divergent revision")
        if any(getattr(state, key) != value for key, value in patch.items()):
            raise WorkspaceRepositoryError("task state replay returned divergent fields")
        if any(ref not in state.source_event_refs for ref in source_refs):
            raise WorkspaceRepositoryError("task state replay lost source provenance")
        return state

    def _validate_identity(self, state: PinnedTaskState) -> None:
        if state.state_id != self._repository.state_id:
            raise RepositoryScopeError("task state identity does not match its binding")

    @staticmethod
    def _validate_state_bounds(state: PinnedTaskState) -> None:
        item_count = sum(
            len(getattr(state, field_name))
            for field_name in (
                *_LIST_FIELDS,
                "artifact_refs",
                "memory_refs",
                "source_event_refs",
            )
        )
        if item_count > MAX_TASK_STATE_ITEMS:
            raise ModelValidationError("task state exceeds the aggregate item budget")
        encoded = json.dumps(
            state.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_TASK_STATE_BYTES:
            raise ModelValidationError("task state exceeds the aggregate byte budget")

    def _authorize_refs(
        self,
        values: Sequence[ResourceRef],
        *,
        field_name: str,
        allowed_kinds: frozenset[str],
    ) -> tuple[ResourceRef, ...]:
        if isinstance(values, (str, bytes, bytearray)):
            raise TypeError(f"{field_name} must be an array")
        if len(values) > MAX_TASK_STATE_ITEMS:
            raise ModelValidationError(f"{field_name} exceeds the item budget")
        authorized: list[ResourceRef] = []
        for raw_ref in values:
            ref = raw_ref if isinstance(raw_ref, ResourceRef) else ResourceRef.from_dict(raw_ref)
            if ref.kind not in allowed_kinds:
                raise RepositoryScopeError(f"{field_name} contains a disallowed reference")
            canonical = self._references.authorize(ref=ref)
            if canonical != ref:
                raise RepositoryScopeError("reference authorizer changed a task-state ref")
            authorized.append(ref)
        return tuple(authorized)


__all__ = [
    "MAX_TASK_STATE_BYTES",
    "MAX_TASK_STATE_ITEMS",
    "TaskStateService",
]
