from __future__ import annotations

from unchain.journal import ResourceRef
from unchain.memory.workspace import MemorySpace
from unchain.memory.workspace.search import IndexHit, WorkspaceSearchService
from unchain.memory.workspace.service import MemoryWorkspaceService

from .fakes import (
    FakeReferenceAuthorizer,
    FakeWorkspaceRepository,
    entry_ref,
    source_event,
)


class FakeVectorIndex:
    def __init__(self) -> None:
        self.hits: tuple[IndexHit, ...] = ()
        self.fail_search = False
        self.fail_upsert = False
        self.indexed = []
        self.superseded = []

    def supersede(self, *, entry_ref: ResourceRef, deleted: bool) -> None:
        self.superseded.append((entry_ref, deleted))
        self.indexed = [
            chunk
            for chunk in self.indexed
            if chunk.entry_ref.resource_id != entry_ref.resource_id
            or chunk.entry_ref.fragment != entry_ref.fragment
        ]

    def upsert(self, chunks) -> None:
        if self.fail_upsert:
            raise RuntimeError("vector offline")
        self.indexed.extend(chunks)

    def search(self, query: str, *, limit: int):
        if self.fail_search:
            raise RuntimeError("vector offline")
        return self.hits[:limit]


def build_search_stack(vector=None):
    space = MemorySpace("space-chat", "chat", "Chat memory", "Synthetic", 1)
    repository = FakeWorkspaceRepository(space)
    event = source_event()
    references = FakeReferenceAuthorizer("chat-binding", {event})
    search = WorkspaceSearchService(repository=repository, vector_index=vector)
    service = MemoryWorkspaceService(
        repository=repository,
        mutations=repository,
        content=repository,
        history=repository,
        links=repository,
        references=references,
        search=search,
    )
    return service, search, repository


def seed(service: MemoryWorkspaceService):
    roadmap = service.write_markdown(
        path="/plans/Roadmap.md",
        description="Release milestones and rollout gates",
        content="P0 then canary",
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="seed-roadmap",
    )
    security = service.write_markdown(
        path="/notes/security.md",
        description="Secret vault and reference authorization",
        content="Fail closed",
        expected_space_revision=2,
        source_refs=(source_event(),),
        operation_id="seed-security",
    )
    return roadmap, security


def test_search_prioritizes_exact_path_and_name_before_fts_results() -> None:
    service, search, _ = build_search_stack()
    roadmap, _ = seed(service)

    by_path = search.search("/plans/Roadmap.md", limit=10)
    by_name = search.search("Roadmap.md", limit=10)

    assert by_path.hits[0].entry == roadmap
    assert by_path.hits[0].matched_by[0] == "exact_path"
    assert by_name.hits[0].entry == roadmap
    assert by_name.hits[0].matched_by[0] == "exact_name"


def test_fts_failure_falls_back_to_bounded_lexical_scan() -> None:
    service, search, repository = build_search_stack()
    _, security = seed(service)
    repository.search_unavailable = True

    result = search.search("reference authorization", limit=10)

    assert result.hits[0].entry == security
    assert "lexical_fallback" in result.hits[0].matched_by
    assert result.lexical_fallback is True


def test_vector_results_are_scope_checked_and_vector_failure_is_fail_open() -> None:
    vector = FakeVectorIndex()
    service, search, _ = build_search_stack(vector)
    roadmap, security = seed(service)
    vector.hits = (
        IndexHit(entry_ref(security), score=0.95, chunk_id="chunk-security"),
        IndexHit(
            ResourceRef("memory", "foreign-entry", 1, "space-foreign"),
            score=0.99,
            chunk_id="chunk-foreign",
        ),
    )

    result = search.search("vault", limit=10)

    assert security in [hit.entry for hit in result.hits]
    assert all(hit.entry.entry_id != "foreign-entry" for hit in result.hits)
    vector.fail_search = True
    fail_open = search.search("Roadmap.md", limit=10)
    assert fail_open.hits[0].entry == roadmap
    assert fail_open.vector_error == "unavailable"


def test_vector_index_failure_never_blocks_workspace_writes() -> None:
    vector = FakeVectorIndex()
    vector.fail_upsert = True
    service, _, repository = build_search_stack(vector)

    entry = service.write_markdown(
        path="/notes/vector-fallback.md",
        description="A write that survives vector downtime",
        content="durable first",
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="vector-write-fallback",
    )

    assert repository.entries[entry.entry_id] == entry


def test_markdown_body_is_chunked_into_the_optional_vector_index() -> None:
    vector = FakeVectorIndex()
    service, _, _ = build_search_stack(vector)
    fact = "The canary advances only after the recovery matrix is green."

    entry = service.write_markdown(
        path="/notes/canary-gate.md",
        description="Canary rollout gate",
        content=(fact + "\n") * 300,
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="index-markdown-body",
    )

    entry_chunks = [chunk for chunk in vector.indexed if chunk.entry_ref == entry_ref(entry)]
    assert len(entry_chunks) >= 2
    assert any(fact in chunk.text for chunk in entry_chunks)
    assert all(len(chunk.text) <= 10_000 for chunk in entry_chunks)


def test_stale_vector_revision_is_ignored_after_an_entry_update() -> None:
    vector = FakeVectorIndex()
    service, search, _ = build_search_stack(vector)
    original = service.write_markdown(
        path="/notes/revision.md",
        description="Current revision validation",
        content="old semantic payload",
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="index-old-revision",
    )
    updated = service.write_markdown(
        entry_ref=entry_ref(original),
        path=original.path,
        description=original.description,
        content="new semantic payload",
        expected_space_revision=2,
        source_refs=(source_event(),),
        operation_id="index-new-revision",
    )
    vector.hits = (
        IndexHit(entry_ref(original), score=0.99, chunk_id="stale-old-revision"),
    )

    result = search.search("semantic-only-vector-query", limit=10)

    assert updated.revision == 2
    assert all("vector" not in hit.matched_by for hit in result.hits)


def test_vector_index_supersedes_prior_revisions_and_archived_entries() -> None:
    vector = FakeVectorIndex()
    service, _, _ = build_search_stack(vector)
    original = service.write_markdown(
        path="/notes/vector-lifecycle.md",
        description="Vector lifecycle",
        content="revision one",
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="vector-lifecycle-v1",
    )
    updated = service.write_markdown(
        entry_ref=entry_ref(original),
        path=original.path,
        description=original.description,
        content="revision two",
        expected_space_revision=2,
        source_refs=(source_event(),),
        operation_id="vector-lifecycle-v2",
    )

    assert vector.indexed
    assert {chunk.entry_ref for chunk in vector.indexed} == {entry_ref(updated)}

    archived = service.archive(
        ref=entry_ref(updated),
        expected_space_revision=3,
        source_refs=(source_event(),),
        operation_id="vector-lifecycle-archive",
    )

    assert archived.deleted is True
    assert vector.indexed == []
    assert vector.superseded == [
        (entry_ref(original), False),
        (entry_ref(updated), False),
        (entry_ref(archived), True),
    ]


def test_stale_vector_top_k_cannot_starve_a_current_revision() -> None:
    vector = FakeVectorIndex()
    service, search, _ = build_search_stack(vector)
    original = service.write_markdown(
        path="/notes/top-k.md",
        description="Current vector result",
        content="old",
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="top-k-v1",
    )
    current = service.write_markdown(
        entry_ref=entry_ref(original),
        path=original.path,
        description=original.description,
        content="current",
        expected_space_revision=2,
        source_refs=(source_event(),),
        operation_id="top-k-v2",
    )
    vector.hits = tuple(
        IndexHit(entry_ref(original), score=0.99, chunk_id=f"stale-{index}")
        for index in range(20)
    ) + (IndexHit(entry_ref(current), score=0.80, chunk_id="current"),)

    result = search.search("opaque-vector-query", limit=1)

    assert [hit.entry for hit in result.hits] == [current]
    assert "vector" in result.hits[0].matched_by


def test_fts_results_are_resolved_against_the_current_entry_revision() -> None:
    service, search, repository = build_search_stack()
    original = service.write_markdown(
        path="/notes/fts-revision.md",
        description="FTS revision validation",
        content="old",
        expected_space_revision=1,
        source_refs=(source_event(),),
        operation_id="fts-revision-v1",
    )
    service.write_markdown(
        entry_ref=entry_ref(original),
        path=original.path,
        description="Current description does not contain the lookup term",
        content="current",
        expected_space_revision=2,
        source_refs=(source_event(),),
        operation_id="fts-revision-v2",
    )
    repository.search_override = (original,)

    result = search.search("legacy-only-fts-term", limit=10)

    assert result.hits == ()


def test_search_supports_tag_backlink_and_recency_domains() -> None:
    service, _, _ = build_search_stack()
    event = source_event()
    decision = service.write_markdown(
        path="/notes/release-decision.md",
        description="Release decision",
        content="Ship after recovery tests pass.",
        tags=("decision", "release"),
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="domain-decision",
    )
    evidence = service.write_markdown(
        path="/notes/release-evidence.md",
        description="Evidence for the release decision",
        content="Recovery tests are green.",
        tags=("evidence",),
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="domain-evidence",
    )
    service.link(
        source_ref=entry_ref(decision),
        target_ref=entry_ref(evidence),
        relation="supports",
        expected_space_revision=3,
        source_refs=(event,),
        operation_id="domain-backlink",
    )
    newest = service.write_markdown(
        path="/notes/recent-update.md",
        description="Most recent workspace update",
        content="Newest",
        tags=("update",),
        expected_space_revision=4,
        source_refs=(event,),
        operation_id="domain-recent",
    )

    by_tag = service.search("", tags=("decision",), limit=10)
    backlinks = service.search("", backlink_ref=entry_ref(evidence), limit=10)
    recent = service.search("", recent_first=True, limit=10)

    assert [hit.entry for hit in by_tag.hits] == [decision]
    assert "tag" in by_tag.hits[0].matched_by
    assert [hit.entry for hit in backlinks.hits] == [decision]
    assert "backlink" in backlinks.hits[0].matched_by
    assert recent.hits[0].entry == newest
    assert [hit.entry.updated_seq for hit in recent.hits] == sorted(
        (hit.entry.updated_seq for hit in recent.hits),
        reverse=True,
    )


def test_tag_mutation_is_revisioned_and_search_uses_only_current_tags() -> None:
    service, _, repository = build_search_stack()
    event = source_event()
    created = service.write_markdown(
        path="/notes/tagged.md",
        description="Tag revision test",
        content="v1",
        tags=("draft",),
        expected_space_revision=1,
        source_refs=(event,),
        operation_id="tagged-v1",
    )
    updated = service.write_markdown(
        entry_ref=entry_ref(created),
        path=created.path,
        description=created.description,
        content="v2",
        tags=("confirmed",),
        expected_space_revision=2,
        source_refs=(event,),
        operation_id="tagged-v2",
    )

    assert updated.tags == ("confirmed",)
    assert service.search("", tags=("draft",)).hits == ()
    assert service.search("", tags=("confirmed",)).hits[0].entry == updated
    assert repository.revisions[created.entry_id][1].tags == ("draft",)


def test_search_reports_when_bounded_workspace_scan_is_truncated() -> None:
    space = MemorySpace("space-chat", "chat", "Chat memory", "Synthetic", 1)
    repository = FakeWorkspaceRepository(space)
    event = source_event()
    references = FakeReferenceAuthorizer("chat-binding", {event})
    search = WorkspaceSearchService(
        repository=repository,
        link_repository=repository,
        max_scan_entries=1,
    )
    service = MemoryWorkspaceService(
        repository=repository,
        mutations=repository,
        content=repository,
        history=repository,
        links=repository,
        references=references,
        search=search,
    )
    for index in range(2):
        service.write_markdown(
            path=f"/notes/bounded-{index}.md",
            description=f"Bounded scan entry {index}",
            content=f"entry {index}",
            expected_space_revision=1 + index,
            source_refs=(event,),
            operation_id=f"bounded-scan-{index}",
        )

    result = service.search("", recent_first=True, limit=10)

    assert result.scan_truncated is True
