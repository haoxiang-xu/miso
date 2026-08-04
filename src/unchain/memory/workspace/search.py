from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol, Sequence

from unchain.journal import ModelValidationError, ResourceRef

from .models import MemoryEntry, MemoryEntryKind, MemoryLink, canonical_memory_tags
from .ports import (
    BoundMemoryWorkspaceRepository,
    BoundWorkspaceLinkRepository,
    RepositoryConflictError,
    RepositoryNotFoundError,
    RepositoryScopeError,
    RepositorySearchUnavailableError,
    WorkspaceRepositoryError,
)


MAX_SEARCH_RESULTS = 100
MAX_SCAN_ENTRIES = 2_000
MAX_VECTOR_CHUNK_CHARS = 10_000
_VECTOR_BODY_CHARS = 8_000
_VECTOR_BODY_OVERLAP = 256
_VECTOR_METADATA_CHARS = 1_500
_SCAN_PAGE_SIZE = 200


def _query(value: object, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise TypeError("query must be text")
    normalized = unicodedata.normalize("NFC", value.strip())
    if (
        (not normalized and not allow_empty)
        or len(normalized) > 4096
        or "\x00" in normalized
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ModelValidationError("query is invalid")
    return normalized


def _limit(value: object, maximum: int = MAX_SEARCH_RESULTS) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")
    return value


def _entry_ref(entry: MemoryEntry) -> ResourceRef:
    return ResourceRef("memory", entry.entry_id, entry.revision, entry.space_id)


@dataclass(frozen=True)
class IndexChunk:
    chunk_id: str
    entry_ref: ResourceRef
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.chunk_id, str) or not self.chunk_id.strip():
            raise ModelValidationError("chunk_id is invalid")
        if not isinstance(self.entry_ref, ResourceRef):
            object.__setattr__(self, "entry_ref", ResourceRef.from_dict(self.entry_ref))
        if (
            not isinstance(self.text, str)
            or not self.text.strip()
            or "\x00" in self.text
            or len(self.text) > MAX_VECTOR_CHUNK_CHARS
        ):
            raise ModelValidationError("index text is invalid")


@dataclass(frozen=True)
class IndexHit:
    entry_ref: ResourceRef
    score: float
    chunk_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.entry_ref, ResourceRef):
            object.__setattr__(self, "entry_ref", ResourceRef.from_dict(self.entry_ref))
        if (
            isinstance(self.score, bool)
            or not isinstance(self.score, (int, float))
            or not math.isfinite(float(self.score))
            or not 0.0 <= float(self.score) <= 1.0
        ):
            raise ModelValidationError("vector score must be between zero and one")
        object.__setattr__(self, "score", float(self.score))
        if not isinstance(self.chunk_id, str) or not self.chunk_id.strip():
            raise ModelValidationError("chunk_id is invalid")


class VectorIndex(Protocol):
    def supersede(self, *, entry_ref: ResourceRef, deleted: bool) -> None: ...

    def upsert(self, chunks: list[IndexChunk]) -> None: ...

    def search(self, query: str, *, limit: int) -> Sequence[IndexHit]: ...


@dataclass(frozen=True)
class WorkspaceSearchHit:
    entry: MemoryEntry
    score: float
    matched_by: tuple[str, ...]
    source_refs: tuple[ResourceRef, ...]


@dataclass(frozen=True)
class WorkspaceSearchResult:
    hits: tuple[WorkspaceSearchHit, ...]
    lexical_fallback: bool = False
    vector_error: str = ""
    scan_truncated: bool = False


@dataclass(frozen=True)
class _EntryScan:
    entries: tuple[MemoryEntry, ...]
    truncated: bool


class WorkspaceSearchService:
    """Hybrid search whose untrusted vector output is resolved through a bound repo."""

    def __init__(
        self,
        *,
        repository: BoundMemoryWorkspaceRepository,
        vector_index: VectorIndex | None = None,
        link_repository: BoundWorkspaceLinkRepository | None = None,
        max_scan_entries: int = MAX_SCAN_ENTRIES,
    ) -> None:
        if not isinstance(repository, BoundMemoryWorkspaceRepository):
            raise TypeError("repository must be a BoundMemoryWorkspaceRepository")
        if (
            isinstance(max_scan_entries, bool)
            or not isinstance(max_scan_entries, int)
            or max_scan_entries < 1
            or max_scan_entries > 100_000
        ):
            raise ValueError("max_scan_entries is invalid")
        self._repository = repository
        self._vector_index = vector_index
        if link_repository is not None:
            if not isinstance(link_repository, BoundWorkspaceLinkRepository):
                raise TypeError("link_repository must be a bound link repository")
            if link_repository.space.space_id != repository.space.space_id:
                raise RepositoryScopeError("link repository belongs to another workspace")
        self._link_repository = link_repository
        self._max_scan_entries = max_scan_entries

    @property
    def repository(self) -> BoundMemoryWorkspaceRepository:
        return self._repository

    @property
    def link_repository(self) -> BoundWorkspaceLinkRepository | None:
        return self._link_repository

    def with_link_repository(
        self,
        link_repository: BoundWorkspaceLinkRepository,
    ) -> WorkspaceSearchService:
        return WorkspaceSearchService(
            repository=self._repository,
            vector_index=self._vector_index,
            link_repository=link_repository,
            max_scan_entries=self._max_scan_entries,
        )

    def index_entry(self, entry: MemoryEntry, *, content: bytes | None = None) -> bool:
        self._validate_entry(entry)
        if self._vector_index is None:
            return False
        if content is not None and not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        try:
            self._vector_index.supersede(
                entry_ref=_entry_ref(entry),
                deleted=entry.deleted,
            )
        except Exception:
            return False
        if entry.deleted:
            return True
        metadata = "\n".join(
            (entry.path, entry.name, entry.description, " ".join(entry.tags))
        ).strip()
        metadata = metadata[:_VECTOR_METADATA_CHARS]
        digest = hashlib.sha256(
            f"{entry.space_id}\0{entry.entry_id}\0{entry.revision}".encode("utf-8")
        ).hexdigest()
        bodies: list[str] = []
        if entry.kind is MemoryEntryKind.MARKDOWN and content:
            body = content.decode("utf-8", errors="replace")
            start = 0
            while start < len(body):
                bodies.append(body[start : start + _VECTOR_BODY_CHARS])
                if start + _VECTOR_BODY_CHARS >= len(body):
                    break
                start += _VECTOR_BODY_CHARS - _VECTOR_BODY_OVERLAP
        if not bodies:
            bodies.append("")
        chunks = [
            IndexChunk(
                chunk_id=f"workspace-{digest[:24]}-{ordinal}",
                entry_ref=_entry_ref(entry),
                text=(f"{metadata}\n\n{body}" if body else metadata),
            )
            for ordinal, body in enumerate(bodies)
        ]
        try:
            self._vector_index.upsert(chunks)
        except Exception:
            return False
        return True

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        tags: Sequence[str] = (),
        backlink_ref: ResourceRef | None = None,
        recent_first: bool = False,
    ) -> WorkspaceSearchResult:
        normalized_tags = canonical_memory_tags(tags)
        if not isinstance(recent_first, bool):
            raise TypeError("recent_first must be a boolean")
        normalized_query = _query(
            query,
            allow_empty=bool(normalized_tags or backlink_ref is not None or recent_first),
        )
        result_limit = _limit(limit)
        scan = self._scan_entries()
        scanned = scan.entries
        current_entries = {entry.entry_id: entry for entry in scanned}
        ranked: dict[tuple[str, int], dict[str, object]] = {}
        needle = normalized_query.casefold()
        tokens = tuple(re.findall(r"\w+", needle, flags=re.UNICODE))

        for entry in scanned:
            path_match = entry.path.casefold() == needle
            name_match = entry.name.casefold() == needle
            haystack = (
                f"{entry.path}\n{entry.name}\n{entry.description}\n{' '.join(entry.tags)}"
            ).casefold()
            token_match = bool(tokens) and all(token in haystack for token in tokens)
            tag_match = bool(normalized_tags) and all(
                requested.casefold() in {tag.casefold() for tag in entry.tags}
                for requested in normalized_tags
            )
            if path_match:
                self._merge(ranked, entry, 1.0, "exact_path")
            if name_match:
                self._merge(ranked, entry, 0.98, "exact_name")
            if token_match:
                self._merge(ranked, entry, 0.55, "lexical_fallback")
            if tag_match:
                self._merge(ranked, entry, 0.88, "tag")

        lexical_fallback = False
        if normalized_query:
            try:
                lexical_entries = self._repository.search(
                    query=normalized_query,
                    limit=MAX_SEARCH_RESULTS,
                )
            except RepositorySearchUnavailableError:
                lexical_entries = ()
                lexical_fallback = True
        else:
            lexical_entries = ()
        for entry in lexical_entries:
            self._validate_entry(entry)
            current = current_entries.get(entry.entry_id)
            if current is None or current != entry or entry.deleted:
                continue
            self._merge(ranked, current, 0.75, "fts")

        if backlink_ref is not None:
            if (
                not isinstance(backlink_ref, ResourceRef)
                or backlink_ref.kind != "memory"
                or backlink_ref.fragment != self._repository.space.space_id
            ):
                raise RepositoryScopeError("backlink reference belongs to another workspace")
            target = current_entries.get(backlink_ref.resource_id)
            if target is None or _entry_ref(target) != backlink_ref:
                raise RepositoryConflictError("backlink target reference is not current")
            if self._link_repository is None:
                raise RepositorySearchUnavailableError("backlink index is unavailable")
            backlinks = self._link_repository.list_backlinks(
                target_entry_ref=backlink_ref,
                limit=MAX_SEARCH_RESULTS,
            )
            if len(backlinks) > MAX_SEARCH_RESULTS:
                raise WorkspaceRepositoryError("backlink capability exceeded its limit")
            for link in backlinks:
                if not isinstance(link, MemoryLink) or link.target_ref != backlink_ref:
                    raise RepositoryScopeError("backlink capability returned a foreign relation")
                source = current_entries.get(link.source_entry_ref.resource_id)
                if source is None or _entry_ref(source) != link.source_entry_ref:
                    continue
                self._merge(ranked, source, 0.90, "backlink")

        vector_error = ""
        if self._vector_index is not None and normalized_query:
            try:
                vector_hits = self._vector_index.search(
                    normalized_query,
                    limit=MAX_SEARCH_RESULTS,
                )
            except Exception:
                vector_hits = ()
                vector_error = "unavailable"
            for raw_hit in vector_hits:
                try:
                    hit = raw_hit if isinstance(raw_hit, IndexHit) else IndexHit(**raw_hit)
                    current = current_entries.get(hit.entry_ref.resource_id)
                    if current is None or _entry_ref(current) != hit.entry_ref:
                        continue
                    entry = self._repository.read_entry(ref=hit.entry_ref)
                    self._validate_entry(entry)
                except (
                    ModelValidationError,
                    RepositoryNotFoundError,
                    RepositoryScopeError,
                    TypeError,
                    ValueError,
                ):
                    continue
                if entry.deleted:
                    continue
                if (
                    entry.entry_id != hit.entry_ref.resource_id
                    or entry.revision != hit.entry_ref.revision
                ):
                    continue
                self._merge(
                    ranked,
                    entry,
                    0.60 + (0.35 * hit.score),
                    "vector",
                )

        if recent_first:
            if not ranked and not (normalized_query or normalized_tags or backlink_ref):
                for entry in scanned:
                    self._merge(ranked, entry, 0.50, "recent")
            else:
                for item in ranked.values():
                    if "recent" not in item["matched_by"]:
                        item["matched_by"].append("recent")

        ordered = sorted(
            ranked.values(),
            key=lambda item: (
                -float(item["score"]),
                -item["entry"].updated_seq if recent_first else 0,
                item["entry"].path.casefold(),
                item["entry"].entry_id,
            ),
        )[:result_limit]
        return WorkspaceSearchResult(
            hits=tuple(
                WorkspaceSearchHit(
                    entry=item["entry"],
                    score=float(item["score"]),
                    matched_by=tuple(item["matched_by"]),
                    source_refs=item["entry"].source_refs,
                )
                for item in ordered
            ),
            lexical_fallback=lexical_fallback,
            vector_error=vector_error,
            scan_truncated=scan.truncated,
        )

    def _scan_entries(self) -> _EntryScan:
        entries: list[MemoryEntry] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        truncated = False
        while len(entries) < self._max_scan_entries:
            page = self._repository.list_entries(
                parent_path="/",
                include_deleted=False,
                limit=min(_SCAN_PAGE_SIZE, self._max_scan_entries - len(entries)),
                cursor=cursor,
            )
            for entry in page.entries:
                self._validate_entry(entry)
                entries.append(entry)
            if not page.has_more:
                break
            if page.next_cursor is None or page.next_cursor in seen_cursors:
                raise WorkspaceRepositoryError("workspace pagination did not advance")
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
            if len(entries) >= self._max_scan_entries:
                truncated = True
                break
        return _EntryScan(tuple(entries), truncated)

    def _validate_entry(self, entry: MemoryEntry) -> None:
        if not isinstance(entry, MemoryEntry):
            raise TypeError("repository returned a non-entry result")
        if entry.space_id != self._repository.space.space_id:
            raise RepositoryScopeError("repository returned a foreign workspace entry")

    @staticmethod
    def _merge(
        ranked: dict[tuple[str, int], dict[str, object]],
        entry: MemoryEntry,
        score: float,
        matched_by: str,
    ) -> None:
        key = (entry.entry_id, entry.revision)
        existing = ranked.get(key)
        if existing is None:
            ranked[key] = {
                "entry": entry,
                "score": score,
                "matched_by": [matched_by],
            }
            return
        existing["score"] = max(float(existing["score"]), score)
        sources = existing["matched_by"]
        if matched_by not in sources:
            sources.append(matched_by)


__all__ = [
    "IndexChunk",
    "IndexHit",
    "MAX_SCAN_ENTRIES",
    "MAX_SEARCH_RESULTS",
    "MAX_VECTOR_CHUNK_CHARS",
    "VectorIndex",
    "WorkspaceSearchHit",
    "WorkspaceSearchResult",
    "WorkspaceSearchService",
]
