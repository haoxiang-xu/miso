"""Provider-neutral declaration of semantic reference slots in journal events.

The contract is intentionally descriptive.  It identifies the only payload paths
that may carry resource references after an event has been normalized; it does
not parse reference values, decode product-specific URIs, or grant access to the
referenced resource.  Those responsibilities belong to the storage authority at
the integration boundary.

Paths are relative to the normalized semantic event payload.  Alias paths in one
group name the same logical reference.  Free-text roots are opaque: ref-looking
keys below one of those roots never acquire reference semantics.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TypeAlias


SemanticPath: TypeAlias = tuple[str, ...]


def _normalized_path(path: Sequence[str]) -> SemanticPath:
    if isinstance(path, (str, bytes, bytearray)):
        raise TypeError("a semantic path must be a sequence of path segments")
    normalized = tuple(path)
    if not normalized or any(
        not isinstance(segment, str) or not segment for segment in normalized
    ):
        raise ValueError("semantic path segments must be non-empty strings")
    return normalized


@dataclass(frozen=True, slots=True)
class SemanticRefGroup:
    """One logical ref slot and its accepted alias paths."""

    name: str
    paths: tuple[SemanticPath, ...]
    allowed_kinds: tuple[str, ...]
    required: bool
    repeated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("semantic ref group name must be a non-empty string")
        paths = tuple(_normalized_path(path) for path in self.paths)
        if not paths or len(set(paths)) != len(paths):
            raise ValueError("semantic ref group paths must be non-empty and unique")
        kinds = tuple(self.allowed_kinds)
        if (
            not kinds
            or len(set(kinds)) != len(kinds)
            or any(not isinstance(kind, str) or not kind for kind in kinds)
        ):
            raise ValueError("allowed resource kinds must be non-empty and unique")
        if not isinstance(self.required, bool) or not isinstance(self.repeated, bool):
            raise TypeError("required and repeated must be booleans")
        object.__setattr__(self, "paths", paths)
        object.__setattr__(self, "allowed_kinds", kinds)


@dataclass(frozen=True, slots=True)
class SemanticRefContract:
    """Immutable ref and free-text declaration shared by related event types."""

    name: str
    event_types: tuple[str, ...]
    groups: tuple[SemanticRefGroup, ...] = ()
    free_text_roots: tuple[SemanticPath, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("semantic ref contract name must be a non-empty string")
        event_types = tuple(self.event_types)
        if (
            not event_types
            or len(set(event_types)) != len(event_types)
            or any(
                not isinstance(event_type, str) or not event_type
                for event_type in event_types
            )
        ):
            raise ValueError("event types must be non-empty and unique")
        groups = tuple(self.groups)
        if any(not isinstance(group, SemanticRefGroup) for group in groups):
            raise TypeError("groups must contain SemanticRefGroup records")
        if len({group.name for group in groups}) != len(groups):
            raise ValueError("semantic ref group names must be unique")
        ref_paths = tuple(path for group in groups for path in group.paths)
        if len(set(ref_paths)) != len(ref_paths):
            raise ValueError("a semantic ref path may belong to only one group")
        free_text_roots = tuple(
            _normalized_path(path) for path in self.free_text_roots
        )
        if len(set(free_text_roots)) != len(free_text_roots):
            raise ValueError("free-text roots must be unique")
        if any(
            ref_path[: len(root)] == root
            for ref_path in ref_paths
            for root in free_text_roots
        ):
            raise ValueError("a semantic ref path cannot be inside free text")
        object.__setattr__(self, "event_types", event_types)
        object.__setattr__(self, "groups", groups)
        object.__setattr__(self, "free_text_roots", free_text_roots)

    def ref_group_for_path(
        self,
        path: Sequence[str],
    ) -> SemanticRefGroup | None:
        """Return the declared group only for an exact normalized payload path."""

        normalized = _normalized_path(path)
        for group in self.groups:
            if normalized in group.paths:
                return group
        return None

    def is_free_text_path(self, path: Sequence[str]) -> bool:
        """Return whether a path is at or below a declared opaque text root."""

        normalized = _normalized_path(path)
        return any(
            normalized[: len(root)] == root for root in self.free_text_roots
        )


_CONTRACTS = (
    SemanticRefContract(
        name="message",
        event_types=("message.user", "message.assistant"),
        groups=(
            SemanticRefGroup(
                name="attachments",
                paths=(("attachment_refs",),),
                allowed_kinds=("artifact",),
                required=False,
                repeated=True,
            ),
        ),
        free_text_roots=(("message", "content"),),
    ),
    SemanticRefContract(
        name="tool_call",
        event_types=("tool_call",),
        free_text_roots=(("arguments",),),
    ),
    SemanticRefContract(
        name="tool_result",
        event_types=("tool_result", "tool.result"),
        groups=(
            SemanticRefGroup(
                name="full_output",
                paths=(("full_output_ref",),),
                allowed_kinds=("artifact",),
                required=True,
            ),
        ),
        free_text_roots=(("result",), ("preview",)),
    ),
    SemanticRefContract(
        name="artifact",
        event_types=(
            "artifact_created",
            "artifact_updated",
            "artifact.created",
            "artifact.updated",
            "artifact.recorded",
        ),
        groups=(
            SemanticRefGroup(
                name="artifact",
                paths=(
                    ("artifact_ref",),
                    ("artifact", "artifact_ref"),
                    ("artifact", "content_ref"),
                    ("artifact", "ref"),
                ),
                allowed_kinds=("artifact",),
                required=True,
            ),
        ),
        free_text_roots=(
            ("preview",),
            ("description",),
            ("url",),
            ("artifact", "preview"),
            ("artifact", "description"),
            ("artifact", "url"),
        ),
    ),
    SemanticRefContract(
        name="handoff",
        event_types=(
            "subagent_completed",
            "subagent_failed",
            "subagent_cancelled",
            "subagent_canceled",
            "agent_thread_completed",
            "agent_thread_failed",
            "subagent_return_handoff_completed",
            "handoff.recorded",
        ),
        groups=(
            SemanticRefGroup(
                name="full_output",
                paths=(
                    ("handoff_envelope", "full_output_ref"),
                    ("full_output_ref",),
                    ("handoff_envelope", "handoff_ref"),
                    ("handoff_ref",),
                    ("handoff_envelope", "artifact_ref"),
                    ("artifact_ref",),
                    ("handoff_envelope", "content_ref"),
                    ("content_ref",),
                ),
                allowed_kinds=("artifact",),
                required=True,
            ),
            SemanticRefGroup(
                name="artifacts",
                paths=(
                    ("handoff_envelope", "artifact_refs"),
                    ("artifact_refs",),
                ),
                allowed_kinds=("artifact",),
                required=False,
                repeated=True,
            ),
        ),
        free_text_roots=(
            ("summary",),
            ("preview",),
            ("description",),
            ("url",),
            ("handoff_envelope", "summary"),
            ("handoff_envelope", "preview"),
            ("handoff_envelope", "description"),
            ("handoff_envelope", "url"),
        ),
    ),
    SemanticRefContract(
        name="interaction",
        event_types=(
            "interaction_requested",
            "tool_confirmation_requested",
            "human_input_requested",
        ),
        groups=(
            SemanticRefGroup(
                name="content",
                paths=(("content_ref",),),
                allowed_kinds=(
                    "artifact",
                    "checkpoint",
                    "context_event",
                    "memory",
                ),
                required=False,
            ),
        ),
        free_text_roots=(
            ("interaction_request",),
            ("question",),
            ("description",),
            ("preview",),
            ("url",),
        ),
    ),
)


def _contract_index() -> MappingProxyType[str, SemanticRefContract]:
    indexed: dict[str, SemanticRefContract] = {}
    for contract in _CONTRACTS:
        for event_type in contract.event_types:
            if event_type in indexed:
                raise ValueError(f"duplicate semantic event type: {event_type}")
            indexed[event_type] = contract
    return MappingProxyType(indexed)


_CONTRACT_BY_EVENT_TYPE = _contract_index()


def semantic_ref_contract(event_type: str) -> SemanticRefContract | None:
    """Look up an exact event type; unknown types have no ref semantics."""

    if not isinstance(event_type, str):
        raise TypeError("event_type must be a string")
    return _CONTRACT_BY_EVENT_TYPE.get(event_type)


def semantic_ref_contracts() -> tuple[SemanticRefContract, ...]:
    """Return every immutable semantic reference contract in stable order."""

    return _CONTRACTS


__all__ = (
    "SemanticPath",
    "SemanticRefContract",
    "SemanticRefGroup",
    "semantic_ref_contract",
    "semantic_ref_contracts",
)
