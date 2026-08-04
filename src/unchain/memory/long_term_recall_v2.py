from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import ClassVar

from unchain.journal import ModelValidationError, ResourceRef
from unchain.memory.workspace.models import MemoryEntry, MemoryEntryKind
from unchain.memory.workspace.search import WorkspaceSearchHit
from unchain.memory.workspace.service import LongTermMemoryService


MAX_CONTEXT_REFERENCES = 5
MAX_CANDIDATE_RESULTS = 20
MAX_FIRST_MESSAGE_CHARS = 4096
MAX_DESCRIPTOR_PREVIEW_CHARS = 512
MIN_HIGH_CONFIDENCE_SCORE = 0.75

_MATCH_PRIORITY = {
    "exact_path": 0,
    "exact_name": 1,
    "tag": 2,
    "backlink": 3,
    "fts": 4,
    "vector": 5,
    "lexical_fallback": 6,
    "recent": 7,
}
_SEMANTIC_TAG_PREFIXES = ("semantic:", "semantic_key:")


class LongTermRecallDisposition(StrEnum):
    """Host action implied by one bounded first-message lookup."""

    NONE = "none"
    CONTEXT_REFERENCES = "context_references"
    CURATOR_REQUIRED = "curator_required"


def _first_message(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("first_user_message must be text")
    normalized = unicodedata.normalize("NFC", value.strip())
    if (
        not normalized
        or len(normalized) > MAX_FIRST_MESSAGE_CHARS
        or "\x00" in normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError("first_user_message is invalid")
    return normalized


def _output_limit(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 1
        or value > MAX_CONTEXT_REFERENCES
    ):
        raise ValueError(f"limit must be between 1 and {MAX_CONTEXT_REFERENCES}")
    return value


def _entry_ref(entry: MemoryEntry) -> ResourceRef:
    return ResourceRef(
        "memory",
        entry.entry_id,
        entry.revision,
        entry.space_id,
    )


def _descriptor_preview(entry: MemoryEntry) -> str:
    descriptor = entry.description.strip() or f"{entry.name} ({entry.kind.value})"
    return descriptor[:MAX_DESCRIPTOR_PREVIEW_CHARS]


def _canonical_matches(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        sorted(
            set(values),
            key=lambda value: (_MATCH_PRIORITY.get(value, 100), value),
        )
    )


def _semantic_keys(entry: MemoryEntry) -> tuple[str, ...]:
    keys: list[str] = []
    for tag in entry.tags:
        folded = unicodedata.normalize("NFKC", tag).casefold()
        for prefix in _SEMANTIC_TAG_PREFIXES:
            if folded.startswith(prefix) and len(folded) > len(prefix):
                keys.append(folded[len(prefix) :])
                break
    return tuple(sorted(set(keys)))


@dataclass(frozen=True)
class LongTermRecallReference:
    """Bound descriptor for later paging through the normal memory reader."""

    SCHEMA: ClassVar[str] = "unchain.long_term_recall_reference.v1"

    entry_ref: ResourceRef
    path: str
    name: str
    kind: MemoryEntryKind
    media_type: str
    preview: str
    provenance_refs: tuple[ResourceRef, ...]
    score: float
    matched_by: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.entry_ref, ResourceRef):
            raise TypeError("entry_ref must be a ResourceRef")
        if self.entry_ref.kind != "memory" or not self.entry_ref.fragment:
            raise ModelValidationError("entry_ref must identify a bound memory entry")
        if not isinstance(self.path, str) or not self.path.startswith("/"):
            raise ModelValidationError("path must be a virtual memory path")
        if not isinstance(self.name, str) or not self.name:
            raise ModelValidationError("name is invalid")
        if not isinstance(self.kind, MemoryEntryKind):
            object.__setattr__(self, "kind", MemoryEntryKind(self.kind))
        if not isinstance(self.media_type, str):
            raise TypeError("media_type must be text")
        if (
            not isinstance(self.preview, str)
            or len(self.preview) > MAX_DESCRIPTOR_PREVIEW_CHARS
            or "\x00" in self.preview
        ):
            raise ModelValidationError("preview is invalid")
        if not isinstance(self.provenance_refs, tuple) or any(
            not isinstance(ref, ResourceRef) for ref in self.provenance_refs
        ):
            raise TypeError("provenance_refs must be ResourceRef records")
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
            or not math.isfinite(float(self.score))
            or not 0.0 <= float(self.score) <= 1.0
        ):
            raise ModelValidationError("score must be between zero and one")
        object.__setattr__(self, "score", round(float(self.score), 6))
        if (
            not isinstance(self.matched_by, tuple)
            or not self.matched_by
            or any(not isinstance(value, str) or not value for value in self.matched_by)
        ):
            raise ModelValidationError("matched_by must identify retrieval evidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "entry_ref": self.entry_ref.to_dict(),
            "path": self.path,
            "name": self.name,
            "kind": self.kind.value,
            "media_type": self.media_type,
            "preview": self.preview,
            "provenance_refs": [ref.to_dict() for ref in self.provenance_refs],
            "score": self.score,
            "matched_by": list(self.matched_by),
        }


@dataclass(frozen=True)
class LongTermRecallEnvelope:
    """Provider-neutral references; never a system or developer prompt."""

    SCHEMA: ClassVar[str] = "unchain.long_term_first_message_recall.v1"

    disposition: LongTermRecallDisposition
    namespace: str
    references: tuple[LongTermRecallReference, ...] = ()
    reason: str = ""
    lexical_fallback: bool = False
    vector_unavailable: bool = False
    trusted: bool = field(default=False, init=False)
    placement: str = field(default="context_reference", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.disposition, LongTermRecallDisposition):
            object.__setattr__(
                self,
                "disposition",
                LongTermRecallDisposition(self.disposition),
            )
        if not isinstance(self.namespace, str) or not self.namespace:
            raise ModelValidationError("namespace is invalid")
        if not isinstance(self.references, tuple) or any(
            not isinstance(item, LongTermRecallReference) for item in self.references
        ):
            raise TypeError("references must be recall reference records")
        if len(self.references) > MAX_CONTEXT_REFERENCES:
            raise ModelValidationError("too many context references")
        if not isinstance(self.reason, str):
            raise TypeError("reason must be text")
        if not isinstance(self.lexical_fallback, bool):
            raise TypeError("lexical_fallback must be a boolean")
        if not isinstance(self.vector_unavailable, bool):
            raise TypeError("vector_unavailable must be a boolean")
        if self.disposition is LongTermRecallDisposition.NONE:
            if self.references or self.reason:
                raise ModelValidationError("none disposition cannot carry results")
        elif self.disposition is LongTermRecallDisposition.CONTEXT_REFERENCES:
            if not self.references or self.reason:
                raise ModelValidationError(
                    "context reference disposition requires unambiguous references"
                )
        elif not self.reason:
            raise ModelValidationError("curator disposition requires a reason")
        if any(
            item.entry_ref.fragment != self.references[0].entry_ref.fragment
            for item in self.references[1:]
        ):
            raise ModelValidationError("references span multiple memory spaces")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "disposition": self.disposition.value,
            "namespace": self.namespace,
            "trusted": self.trusted,
            "placement": self.placement,
            "reason": self.reason,
            "references": [item.to_dict() for item in self.references],
            "retrieval": {
                "lexical_fallback": self.lexical_fallback,
                "vector_unavailable": self.vector_unavailable,
            },
        }


class LongTermFirstMessageRecall:
    """Read one host-bound namespace and return bounded, untrusted references."""

    def __init__(self, *, memory: LongTermMemoryService) -> None:
        if not isinstance(memory, LongTermMemoryService):
            raise TypeError("memory must be a LongTermMemoryService")
        self._memory = memory

    @property
    def namespace(self) -> str:
        return self._memory.namespace

    @property
    def binding_id(self) -> str:
        return self._memory.binding_id

    def recall_first_message(
        self,
        first_user_message: str,
        *,
        limit: int = MAX_CONTEXT_REFERENCES,
    ) -> LongTermRecallEnvelope:
        query = _first_message(first_user_message)
        output_limit = _output_limit(limit)
        result = self._memory.search(query, limit=MAX_CANDIDATE_RESULTS)
        candidates = self._high_confidence_candidates(result.hits)
        if not candidates:
            if result.scan_truncated:
                return LongTermRecallEnvelope(
                    disposition=LongTermRecallDisposition.CURATOR_REQUIRED,
                    namespace=self.namespace,
                    reason="multi_step_scan_required",
                    lexical_fallback=result.lexical_fallback,
                    vector_unavailable=bool(result.vector_error),
                )
            return LongTermRecallEnvelope(
                disposition=LongTermRecallDisposition.NONE,
                namespace=self.namespace,
                lexical_fallback=result.lexical_fallback,
                vector_unavailable=bool(result.vector_error),
            )

        reason = self._curator_reason(candidates, scan_truncated=result.scan_truncated)
        references = tuple(self._reference(hit) for hit in candidates[:output_limit])
        return LongTermRecallEnvelope(
            disposition=(
                LongTermRecallDisposition.CURATOR_REQUIRED
                if reason
                else LongTermRecallDisposition.CONTEXT_REFERENCES
            ),
            namespace=self.namespace,
            references=references,
            reason=reason,
            lexical_fallback=result.lexical_fallback,
            vector_unavailable=bool(result.vector_error),
        )

    def _high_confidence_candidates(
        self,
        hits: tuple[WorkspaceSearchHit, ...],
    ) -> tuple[WorkspaceSearchHit, ...]:
        selected: dict[tuple[str, int], WorkspaceSearchHit] = {}
        for hit in hits:
            if not isinstance(hit, WorkspaceSearchHit):
                raise TypeError("long-term search returned an invalid hit")
            entry = hit.entry
            if entry.space_id != self._memory.space.space_id:
                raise ModelValidationError("long-term search crossed its bound space")
            if entry.deleted or hit.score < MIN_HIGH_CONFIDENCE_SCORE:
                continue
            key = (entry.entry_id, entry.revision)
            previous = selected.get(key)
            if previous is None or hit.score > previous.score:
                selected[key] = hit
        return tuple(
            sorted(
                selected.values(),
                key=lambda hit: (
                    -hit.score,
                    hit.entry.path.casefold(),
                    hit.entry.entry_id,
                ),
            )
        )

    @staticmethod
    def _curator_reason(
        candidates: tuple[WorkspaceSearchHit, ...],
        *,
        scan_truncated: bool,
    ) -> str:
        paths: dict[str, str] = {}
        semantics: dict[str, str] = {}
        for hit in candidates:
            entry = hit.entry
            path_key = unicodedata.normalize("NFKC", entry.path).casefold()
            path_owner = paths.setdefault(path_key, entry.entry_id)
            if path_owner != entry.entry_id:
                return "path_conflict"
            for semantic_key in _semantic_keys(entry):
                semantic_owner = semantics.setdefault(semantic_key, entry.entry_id)
                if semantic_owner != entry.entry_id:
                    return "semantic_key_conflict"
        if len(candidates) > MAX_CONTEXT_REFERENCES:
            return "too_many_results"
        if scan_truncated:
            return "multi_step_scan_required"
        return ""

    @staticmethod
    def _reference(hit: WorkspaceSearchHit) -> LongTermRecallReference:
        entry = hit.entry
        return LongTermRecallReference(
            entry_ref=_entry_ref(entry),
            path=entry.path,
            name=entry.name,
            kind=entry.kind,
            media_type=entry.media_type,
            preview=_descriptor_preview(entry),
            provenance_refs=entry.source_refs,
            score=hit.score,
            matched_by=_canonical_matches(hit.matched_by),
        )


__all__ = [
    "LongTermFirstMessageRecall",
    "LongTermRecallDisposition",
    "LongTermRecallEnvelope",
    "LongTermRecallReference",
]
